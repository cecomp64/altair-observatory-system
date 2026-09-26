"""``altaird`` (SPEC §4.1, §11): every Altair worker in one process, each on
its own thread so a slow S3 restore or upload never blocks processing.

| Worker | Every |
|---|---|
| NAS health, collectors (one per rig), night triggers | the rig's ``poll_interval_s`` |
| replicator (S3 backup) | 60 s |
| planner (plan requests: closed nights, reruns, Hub commands) and executor | 30 s |
| Hub sync (outbox, commands, config, heartbeat) | 5 s, when the Hub is enabled |
| restore poller | 30 min |
| notifier and status page | 60 s |
| cleanup | daily at ``storage.cleanup.schedule_local`` |
| catalog backup | daily at ``catalog_backup_local`` (default 11:00) |
| scrub | when due (``storage.verify.scrub_interval_days``) |
| housekeeping (cache eviction, NAS backfill, old work dirs and logs, missing manifests) | hourly |

A worker's exception is logged and the worker carries on at its next tick.
Task Scheduler restarts the whole process if it dies (§4.1).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from altair.catalog.db import Catalog
from altair.config import AltairConfig

log = logging.getLogger("altaird")


@dataclass
class Worker:
    name: str
    interval_s: float
    fn: Callable[[], object]
    runs: int = 0
    errors: int = 0
    last_error: str | None = None
    last_run_at: float | None = None
    thread: threading.Thread | None = field(default=None, repr=False)

    def tick(self) -> None:
        try:
            self.fn()
        except Exception as exc:  # noqa: BLE001 - a worker never takes the daemon down
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("worker %s failed", self.name)
        finally:
            self.runs += 1
            self.last_run_at = time.time()


class Daemon:
    def __init__(self, catalog: Catalog, config: AltairConfig, *, s3=None, hub_client=None,
                 clock: Callable[[], datetime] | None = None, catalog_backup_local: str = "11:00"):
        self.catalog = catalog
        self.config = config
        self.s3 = s3
        self.hub_client = hub_client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.catalog_backup_local = catalog_backup_local
        self.stop_event = threading.Event()
        self.pi_lock = threading.Lock()     # one PixInsight job at a time
        self.workers = self._workers()

    # ── what runs ────────────────────────────────────────────────────────
    def _hub_config(self):
        from altair.hub.config_sync import load_cached

        return load_cached(self.catalog) if self.config.hub.enabled else None

    def _workers(self) -> list[Worker]:
        from altair.collector.rig_worker import RigCollector
        from altair.executor.executor import Executor
        from altair.notify.notifier import Notifier
        from altair.planner.plan import Planner
        from altair.publish.publisher import Publisher
        from altair.storage import nas as nas_mod
        from altair.storage.stager import Stager
        from altair.triggers import Triggers

        cfg = self.config
        stager = Stager(self.catalog, cfg, s3=self.s3, clock=self.clock)
        self.executor = Executor(self.catalog, cfg, stager=stager, clock=self.clock)
        self.planner = Planner(self.catalog, cfg, self._hub_config, clock=self.clock)
        notifier = Notifier(self.catalog, cfg, clock=self.clock)
        publisher = Publisher(self.catalog, cfg)
        workers = []
        if cfg.storage.nas:
            workers.append(Worker("nas_health", 60, lambda: nas_mod.check(self.catalog, cfg)))
        for rig, rig_cfg in cfg.rigs.items():
            if rig_cfg.raw_root:
                collector = RigCollector(self.catalog, cfg, rig, hub_config=self._hub_config, clock=self.clock)
                workers.append(Worker(f"collect:{rig}", rig_cfg.collect.poll_interval_s, collector.poll))
        workers.append(Worker("triggers", 60, Triggers(self.catalog, cfg, clock=self.clock).tick))
        if self.s3 is not None:
            from altair.storage.replicator import Replicator

            workers.append(Worker("replicator", 60, Replicator(self.catalog, cfg, self.s3, clock=self.clock).run_once))
            workers.append(Worker("restore_poller", 1800, stager.poll_restores))
        workers.append(Worker("processing", 30, self.process))
        if cfg.hub.enabled and self.hub_client is not None:
            from altair.hub.sync import HubSync

            sync = HubSync(self.catalog, cfg, self.hub_client, clock=self.clock, status=self.status)
            workers.append(Worker("hub_sync", 5, sync.tick))
        workers.append(Worker("notify", 60, lambda: (notifier.tick(), self.write_status())))
        workers.append(Worker("cleanup", 60, self.daily("cleanup", cfg.storage.cleanup.schedule_local, self.cleanup)))
        if cfg.storage.nas:
            workers.append(Worker("catalog_backup", 60, self.daily("catalog_backup", self.catalog_backup_local, self.backup_catalog)))
        workers.append(Worker("scrub", 3600, self.scrub))
        workers.append(Worker("housekeeping", 3600, lambda: (stager.evict(), publisher.backfill_nas(), self.executor.cleanup(),
                                                             self.manifests())))
        return workers

    # ── worker bodies ────────────────────────────────────────────────────
    def process(self) -> None:
        """Plan what was requested, then run jobs until none is runnable."""
        from altair import winapi

        self.planner.process_requests()
        with self.pi_lock:
            pending = self.executor.pending()
            winapi.keep_awake(pending > 0)   # §4.1: keep the PC awake while jobs wait
            if pending:
                reports = self.executor.run_all()
                if reports:
                    self.write_status()
                    self.planner.process_requests()   # auto-resolution reruns queued by new masters

    def cleanup(self) -> None:
        from altair.storage.cleanup import Cleaner

        Cleaner(self.catalog, self.config, s3=self.s3, clock=self.clock).run()

    def backup_catalog(self) -> None:
        from altair.storage import catalog_backup
        from altair.storage.locations import nas_location

        catalog_backup.backup(self.catalog, self.config, nas_location(self.config))

    def scrub(self) -> None:
        from altair.storage.scrub import Scrubber

        scrubber = Scrubber(self.catalog, self.config, s3=self.s3, clock=self.clock)
        if scrubber.due():
            scrubber.run()

    def manifests(self) -> None:
        from altair.collector import manifest
        from altair.storage.locations import nas_location

        manifest.check_missing(self.catalog, self.config, nas_location(self.config))

    def write_status(self) -> None:
        from altair import status_page

        status_page.write(self.catalog, self.config)

    def daily(self, name: str, at_local: str, fn: Callable[[], object]) -> Callable[[], None]:
        """Run ``fn`` once a day, at the first tick after ``at_local`` (site time)."""
        key = f"daily_last_run:{name}"

        def tick() -> None:
            local = self.clock().astimezone(ZoneInfo(self.config.site.timezone))
            hours, minutes = (int(x) for x in at_local.split(":"))
            if local < local.replace(hour=hours, minute=minutes, second=0, microsecond=0):
                return
            today = local.date().isoformat()
            if self.catalog.get_state(key) == today:
                return
            fn()
            self.catalog.set_state(key, today)
        return tick

    def status(self) -> dict:
        """The heartbeat's view of the daemon (sent to the Hub)."""
        return {"workers": {w.name: {"runs": w.runs, "errors": w.errors, "last_error": w.last_error} for w in self.workers},
                "jobs_pending": self.executor.pending()}

    # ── running ──────────────────────────────────────────────────────────
    def run_once(self) -> dict[str, str | None]:
        """Every worker once, in order (``altair serve --once``; tests)."""
        for worker in self.workers:
            worker.tick()
        return {w.name: w.last_error for w in self.workers}

    def start(self) -> None:
        recovered = self.executor.recover()
        if recovered:
            log.info("recovered jobs %s after a restart", recovered)
        for worker in self.workers:
            worker.thread = threading.Thread(target=self._loop, args=(worker,), name=f"altaird-{worker.name}", daemon=True)
            worker.thread.start()
        log.info("altaird started with %d workers", len(self.workers))

    def _loop(self, worker: Worker) -> None:
        while not self.stop_event.is_set():
            worker.tick()
            self.stop_event.wait(worker.interval_s)

    def stop(self, timeout: float = 30) -> None:
        self.stop_event.set()
        for worker in self.workers:
            if worker.thread:
                worker.thread.join(timeout)

    def serve_forever(self) -> None:
        self.start()
        try:
            while not self.stop_event.wait(1):
                pass
        except KeyboardInterrupt:
            log.info("stopping")
        finally:
            self.stop()
