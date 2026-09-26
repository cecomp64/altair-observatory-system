"""The hub_sync worker in altaird (SPEC §17): pulls config and commands on
their own schedules, drains the outbox, sends heartbeats, and raises
HUB_UNREACHABLE / HUB_REJECTED locally. The Hub being down never blocks
collection, backup or processing: every failure here is caught and retried."""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.frames import adopt_hub_target
from altair.hub import config_sync
from altair.hub.client import HubClient, HubError, HubUnavailable
from altair.hub.commands import CommandRunner
from altair.hub.config_sync import HubConfig
from altair.hub.outbox import Drainer
from altair.issues import raise_issue, resolve_issue

log = logging.getLogger("altair.hub")
HEARTBEAT_S = 300


class HubSync:
    def __init__(self, catalog: Catalog, config: AltairConfig, client: HubClient, *,
                 clock: Callable[[], datetime] | None = None, status: Callable[[], dict] | None = None):
        self.catalog = catalog
        self.config = config
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.status_fn = status or (lambda: {})
        self.hub_config: HubConfig | None = config_sync.load_cached(catalog)
        self.drainer = Drainer(catalog, client, batch_size=config.hub.outbox.batch_size, max_backoff_s=config.hub.outbox.max_backoff_s,
                               on_rejected=self._rejected, on_frame_result=self._frame_result, clock=self.clock)
        self.commands = CommandRunner(catalog, config, client, refresh_config=self.pull_config, hub_config=lambda: self.hub_config)
        self._next: dict[str, datetime] = {}
        self._stop = threading.Event()

    # ── one pass ─────────────────────────────────────────────────────────
    def tick(self) -> dict:
        now = self.clock()
        report: dict = {}
        steps = [("config", self.config.hub.config_poll_s, self.pull_config), ("commands", self.config.hub.command_poll_s, self.commands.poll),
                 ("outbox", 0, self.drainer.drain), ("heartbeat", HEARTBEAT_S, self.heartbeat)]
        reachable = None
        for name, every, step in steps:
            if self._next.get(name, now) > now:
                continue
            try:
                report[name] = step()
                reachable = True if reachable is None else reachable
                if isinstance(report[name], dict) and report[name].get("unavailable"):
                    reachable = False
                self._next[name] = now + timedelta(seconds=every)
            except HubUnavailable as exc:
                log.warning("hub %s: %s", name, exc)
                report[name] = {"error": str(exc)}
                reachable = False
                self._next[name] = now + timedelta(seconds=min(every or 30, 60))
            except HubError as exc:
                log.error("hub %s rejected: %s", name, exc)
                report[name] = {"error": str(exc)}
                self._next[name] = now + timedelta(seconds=every or 30)
        if reachable is not None:
            self._track_reachability(reachable)
            if reachable and self.drainer.due(limit=1):
                report["outbox_after"] = self.drainer.drain()  # e.g. HUB_UNREACHABLE just resolved
        return report

    def sync_now(self) -> dict:
        self._next.clear()
        return self.tick()

    def run_forever(self, interval_s: float = 5.0) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - never let the sync thread die
                log.exception("hub sync tick failed")
            self._stop.wait(interval_s)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name="hub_sync", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()

    # ── steps ────────────────────────────────────────────────────────────
    def pull_config(self) -> HubConfig:
        config, changed = config_sync.pull(self.catalog, self.client)
        self.hub_config = config
        self._check_config(config)
        return config

    def heartbeat(self) -> dict:
        status = {"outbox_depth": self.drainer.depth(), "outbox_parked": self.drainer.parked(), **self.status_fn()}
        return self.client.heartbeat(status)

    # ── callbacks ────────────────────────────────────────────────────────
    def _frame_result(self, result: dict) -> None:
        adopt_hub_target(self.catalog, result["sha256"], result.get("target_id"))

    def _rejected(self, row: sqlite3.Row, error: str) -> None:
        with self.catalog.transaction() as tx:
            raise_issue(tx, self.config, kind="HUB_REJECTED", severity="warning", fingerprint=f"HUB_REJECTED:{row['kind']}:{row['natural_key']}",
                        message=f"The Hub rejected {row['kind']} {row['natural_key']}: {error}. Fix it, then `altair hub outbox retry {row['id']}`.",
                        scope={"outbox_id": row["id"], "kind": row["kind"]})

    def _track_reachability(self, reachable: bool) -> None:
        if reachable:
            self.catalog.set_state("hub_unreachable_since", None)
            self.catalog.set_state("last_hub_contact_at", now_iso())
            with self.catalog.transaction() as tx:
                resolve_issue(tx, self.config, "HUB_UNREACHABLE", resolution="auto:reachable")
            return
        since = self.catalog.get_state("hub_unreachable_since")
        if not since:
            self.catalog.set_state("hub_unreachable_since", self.clock().isoformat())
            return
        down_for = self.clock() - datetime.fromisoformat(since)
        if down_for >= timedelta(minutes=self.config.hub.unreachable_alert_minutes):
            with self.catalog.transaction() as tx:
                raise_issue(tx, self.config, kind="HUB_UNREACHABLE", severity="warning", fingerprint="HUB_UNREACHABLE",
                            message=f"The Hub has been unreachable since {since}. Collection and processing continue; reports are queued.",
                            scope={})

    def _check_config(self, hub_config: HubConfig) -> None:
        """HUB_CONFIG_MISMATCH (blocking for the rig): timezone, focal length
        or camera type differ from the Hub optical train (SPEC §5.1)."""
        with self.catalog.transaction() as tx:
            for name, rig in self.config.rigs.items():
                if not rig.hub:
                    continue
                problems = mismatches(self.config, name, hub_config)
                fingerprint = f"HUB_CONFIG_MISMATCH:{name}"
                if problems:
                    raise_issue(tx, self.config, kind="HUB_CONFIG_MISMATCH", severity="blocking", fingerprint=fingerprint,
                                message=f"Rig {name} doesn't match the Hub: " + "; ".join(problems), scope={"rig": name})
                else:
                    resolve_issue(tx, self.config, fingerprint, resolution="auto:matches")


def mismatches(config: AltairConfig, rig_name: str, hub_config: HubConfig) -> list[str]:
    rig = config.rigs[rig_name]
    telescope = hub_config.telescopes.get(rig.hub.telescope)
    if telescope is None:
        return [f"telescope {rig.hub.telescope} isn't served by this node"]
    problems = []
    if telescope["timezone"] != config.site.timezone:
        problems.append(f"timezone {config.site.timezone} vs Hub {telescope['timezone']}")
    train = hub_config.train(rig.hub.telescope, rig.hub.optical_train)
    if train is None:
        return problems + [f"optical train {rig.hub.optical_train} isn't in the Hub config (are its optics filled in?)"]
    if abs(train["focal_length_mm"] - rig.focal_length_mm) > rig.focal_length_tolerance_mm:
        problems.append(f"focal length {rig.focal_length_mm} mm vs Hub {train['focal_length_mm']} mm")
    camera = config.camera(rig.camera)
    camera_type = camera.type if camera else None
    if camera_type and camera_type != train["camera_type"]:
        problems.append(f"camera type {camera_type} vs Hub {train['camera_type']}")
    return problems
