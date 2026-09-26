"""Offline properties (docs/SYSTEM_ARCHITECTURE.md §11): randomly failing
the Hub during a simulated night never loses an outbox item and never
blocks ingest; after recovery the Hub equals the local catalog."""
import random

import httpx

from altair.hub.reconcile import local_digest

from conftest import TRAIN
from helpers import Clock, index_night, make_sync


def test_random_hub_failures_lose_nothing(catalog, config, tmp_path, fake_hub):
    rng = random.Random(7)
    clock = Clock()
    sync = make_sync(catalog, config, fake_hub, clock)
    sync.pull_config()

    for batch in range(12):
        # Ingest never waits on the Hub.
        index_night(catalog, config, tmp_path, fake_hub, lights=5, start=batch * 5, sub=f"b{batch}")
        fake_hub.fail_next = [rng.choice([httpx.ConnectError("down"), httpx.ReadTimeout("slow"), 500, 502, 503, None]) for _ in range(rng.randint(0, 6))]
        sync.tick()
        clock.advance(rng.choice([5, 30, 120, 900]))
    fake_hub.queue_command(1, "night_ready", {"optical_train": TRAIN, "night": "2026-09-24", "at": "2026-09-25T12:41:00Z", "closed_by": "session_end"})

    # Recovery: the Hub is healthy again.
    fake_hub.fail_next = []
    for _ in range(6):
        clock.advance(1000)
        sync.tick()

    assert sync.drainer.depth() == 0 and sync.drainer.parked() == 0
    local = {r["sha256"] for r in catalog.query("SELECT sha256 FROM frames")}
    assert set(fake_hub.frames) == local
    assert len(local) == 12 * 6
    theirs = [f for f in fake_hub.frames.values() if f["night"] == "2026-09-24"]
    assert len(theirs) == local_digest(catalog, "esprit", "2026-09-24")["frame_count"]
    assert fake_hub.nights[(TRAIN, "2026-09-24")]["state"] == "closed"
    assert fake_hub.contract_violations == []


def test_hub_unreachable_is_raised_after_the_alert_window_and_cleared_on_recovery(catalog, config, fake_hub):
    clock = Clock()
    sync = make_sync(catalog, config, fake_hub, clock)
    for _ in range(4):
        fake_hub.fail_next = [httpx.ConnectError("down")] * 10
        sync.sync_now()
        clock.advance(30 * 60)
    assert catalog.one("SELECT status FROM issues WHERE kind = 'HUB_UNREACHABLE'")["status"] == "open"

    fake_hub.fail_next = []
    sync.sync_now()
    assert catalog.one("SELECT status FROM issues WHERE kind = 'HUB_UNREACHABLE'")["status"] == "resolved"
    assert fake_hub.issues["HUB_UNREACHABLE"]["status"] == "resolved"
    assert fake_hub.heartbeats[-1]["status"]["outbox_depth"] >= 0
