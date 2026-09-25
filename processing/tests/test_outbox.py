import json

import httpx
import pytest

from altair.hub import outbox
from altair.hub.outbox import Drainer

from helpers import Clock, index_night, make_sync


def test_enqueue_rolls_back_with_the_catalog_change(catalog):
    with pytest.raises(RuntimeError):
        with catalog.transaction() as tx:
            outbox.enqueue(tx, "job", "job:1", {"altair_id": 1, "body": {}})
            raise RuntimeError("catalog write failed")
    assert catalog.one("SELECT count(*) AS n FROM hub_outbox")["n"] == 0


def test_frames_reach_the_hub_in_contract_shape(catalog, config, tmp_path, fake_hub):
    index_night(catalog, config, tmp_path, fake_hub, lights=3)
    sync = make_sync(catalog, config, fake_hub)
    stats = sync.drainer.drain()

    assert stats["sent"] == 4
    assert fake_hub.contract_violations == []
    lights = [f for f in fake_hub.frames.values() if f["image_type"] == "light"]
    assert {f["target_id"] for f in lights} == {34}
    assert {f["assignment_source"] for f in lights} == {"header_token"}
    assert all(f["filter"] == "Ha" and f["origin"] == "import" for f in lights)
    assert sync.drainer.depth() == 0


def test_only_the_newest_payload_per_key_is_sent(catalog, fake_hub):
    with catalog.transaction() as tx:
        for status in ("queued", "running", "succeeded"):
            outbox.enqueue(tx, "job", "job:9", {"altair_id": 9, "body": {"kind": "NIGHT_STACK", "status": status}})
    stats = Drainer(catalog, fake_hub.client()).drain()
    assert stats == {"sent": 1, "failed": 0, "parked": 0, "coalesced": 2}
    assert fake_hub.jobs[9]["status"] == "succeeded"


def test_frames_are_batched_at_500(catalog, config, fake_hub):
    with catalog.transaction() as tx:
        for i in range(1200):
            outbox.enqueue(tx, "job", f"job:{i}", {"altair_id": i, "body": {"kind": "MERGE", "status": "queued"}})
    Drainer(catalog, fake_hub.client(), batch_size=500).drain()
    assert len(fake_hub.jobs) == 1200


def test_network_errors_and_5xx_retry_forever_with_backoff(catalog, fake_hub):
    clock = Clock()
    with catalog.transaction() as tx:
        outbox.enqueue(tx, "job", "job:1", {"altair_id": 1, "body": {"kind": "MERGE", "status": "queued"}})
    drainer = Drainer(catalog, fake_hub.client(), max_backoff_s=900, clock=clock)
    for attempt in range(1, 12):
        fake_hub.fail_next = [httpx.ConnectError("down") if attempt % 2 else 503]
        assert drainer.drain().get("unavailable") == 1
        row = catalog.one("SELECT * FROM hub_outbox")
        assert row["parked"] == 0 and row["attempts"] == attempt
        clock.advance(10_000)
    delay = catalog.one("SELECT attempts, next_attempt_at FROM hub_outbox")
    assert delay["attempts"] == 11  # never parked
    assert drainer.drain()["sent"] == 1


def test_4xx_parks_after_five_attempts_and_raises_hub_rejected(catalog, config, fake_hub):
    clock = Clock()
    sync = make_sync(catalog, config, fake_hub, clock)
    with catalog.transaction() as tx:
        outbox.enqueue(tx, "job", "job:1", {"altair_id": 1, "body": {"kind": "MERGE", "status": "queued"}})
    for _ in range(5):
        fake_hub.fail_next = [422]
        sync.drainer.drain()
        clock.advance(10_000)
    assert catalog.one("SELECT parked FROM hub_outbox WHERE kind = 'job'")["parked"] == 1
    issue = catalog.one("SELECT * FROM issues WHERE kind = 'HUB_REJECTED'")
    assert issue["status"] == "open" and "job:1" in issue["message"]

    item = catalog.one("SELECT id FROM hub_outbox WHERE kind = 'job'")["id"]
    sync.drainer.retry(item)
    sync.drainer.drain()
    assert fake_hub.jobs[1]["status"] == "queued"


def test_a_manual_assignment_from_the_hub_is_adopted(catalog, config, tmp_path, fake_hub):
    index_night(catalog, config, tmp_path, fake_hub, lights=1)
    sha = catalog.one("SELECT sha256 FROM frames WHERE image_type = 'light'")["sha256"]
    fake_hub.manual[sha] = 35
    make_sync(catalog, config, fake_hub).drainer.drain()
    row = catalog.one("SELECT hub_target_id, assignment_source FROM frames WHERE sha256 = ?", (sha,))
    assert (row["hub_target_id"], row["assignment_source"]) == (35, "manual")


def test_frame_patches_merge_while_pending(catalog, fake_hub):
    from altair.hub.reporters import enqueue_frame_patch

    with catalog.transaction() as tx:
        enqueue_frame_patch(tx, "a" * 64, {"status": "rejected"})
        enqueue_frame_patch(tx, "a" * 64, {"quality": {"fwhm": 2.1}})
    rows = catalog.query("SELECT payload_json FROM hub_outbox")
    assert len(rows) == 1
    assert json.loads(rows[0]["payload_json"]) == {"sha256": "a" * 64, "status": "rejected", "quality": {"fwhm": 2.1}}
