"""One collector worker per rig (SPEC §7.3). Each poll:

1. probes the rig's raw root (``RIG_UNREACHABLE`` after a while, blocking
   after 12 h, because uncollected frames exist only on the rig);
2. scans it and tracks every file in ``rig_files`` (a file that disappears
   before it was collected raises ``DATA_AT_RISK``);
3. collects files whose size and mtime have been stable for
   ``stable_seconds`` and that open exclusively and parse completely: copy to
   the spool while hashing, read the rig file a second time and compare,
   write the NAS copy (read back and re-hashed), then ingest;
4. picks up session-end markers (standalone sites) and finishes nights that
   are closing: a final pass, the collection manifest, then the close.

Frames only move rig → spool → NAS; nothing on the rig is changed here.
While the NAS isn't usable, nothing is collected and frames wait on the rig.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from altair import nights
from altair.catalog.db import Catalog, now_iso
from altair.collector import files as file_checks
from altair.collector import manifest
from altair.collector.patterns import matches, selected
from altair.config import AltairConfig
from altair.hashing import copy_hashing, sha256_file
from altair.hub.config_sync import HubConfig
from altair.ingest.headers import night_of, parse_date_obs, read_header
from altair.ingest.ingest import ingest
from altair.issues import raise_issue, resolve_issue
from altair.storage import blobs
from altair.storage import nas as nas_health
from altair.storage.locations import FsLocation, IntegrityMismatch, nas_location, remove_file, rig_location
from altair.winapi import exclusive_open_ok

log = logging.getLogger("altair.collector")
MAX_READ_MISMATCHES = 3
BLOCKING_AFTER = timedelta(hours=12)


@dataclass
class PollReport:
    rig: str
    reachable: bool = True
    nas_usable: bool = True
    seen: int = 0
    collected: int = 0
    already_known: int = 0
    failed: list[str] = field(default_factory=list)
    gone: int = 0
    closed: list[str] = field(default_factory=list)


class Throttle:
    """Bandwidth limit shared by one rig's copies (``bandwidth_limit_mbps``)."""

    def __init__(self, mbps: float | None):
        self.bytes_per_s = mbps * 125_000 if mbps else None
        self.start = time.monotonic()
        self.sent = 0

    def __call__(self, n: int) -> None:
        if not self.bytes_per_s:
            return
        self.sent += n
        ahead = self.sent / self.bytes_per_s - (time.monotonic() - self.start)
        if ahead > 0:
            time.sleep(ahead)


class RigCollector:
    def __init__(self, catalog: Catalog, config: AltairConfig, rig: str, *, hub_config: Callable[[], HubConfig | None] = lambda: None,
                 clock: Callable[[], datetime] | None = None, exclusive_open: Callable[[Path], bool] = exclusive_open_ok):
        self.catalog = catalog
        self.config = config
        self.rig = rig
        self.cfg = config.rigs[rig]
        self.hub_config = hub_config
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.exclusive_open = exclusive_open
        source = rig_location(config, rig)
        if source is None:
            raise ValueError(f"rig {rig} has no raw_root")
        self.source: FsLocation = source
        self.location = f"rig:{rig}"
        self.nas = nas_location(config)
        self.spool = config.paths.spool_dir / rig

    # ── the poll ─────────────────────────────────────────────────────────
    def poll(self, *, nas_state: nas_health.Health | None = None) -> PollReport:
        now = self.clock()
        report = PollReport(self.rig)
        if not self.source.reachable():
            report.reachable = False
            self._unreachable(now)
            return report
        self._reachable()
        scanned = self._scan(now, report)
        self._markers(scanned)
        health = nas_state or nas_health.check(self.catalog, self.config, self.nas)
        report.nas_usable = health.usable
        closing = self.catalog.query("SELECT night FROM collections WHERE rig = ? AND state = 'closing'", (self.rig,))
        if health.usable and (self.cfg.collect.during_capture or closing):
            self._collect_stable(now, report)
        if health.usable:
            self._finish_closing(now, report)
        return report

    def collect_now(self) -> PollReport:
        """`altair collect now`: ignore the stability wait (the files are done)."""
        return self.poll()

    # ── reachability ─────────────────────────────────────────────────────
    def _unreachable(self, now: datetime) -> None:
        key = f"rig_unreachable_since:{self.rig}"
        since = self.catalog.get_state(key)
        if since is None:
            self.catalog.set_state(key, now.isoformat())
            since = now.isoformat()
        with self.catalog.transaction() as tx:
            blobs.ensure_location(tx, self.location)
            tx.execute("UPDATE locations SET reachable = 0, last_probe_at = ? WHERE name = ?", (now_iso(), self.location))
            down = now - datetime.fromisoformat(since)
            if down >= timedelta(minutes=self.cfg.unreachable_alert_minutes):
                severity = "blocking" if down >= BLOCKING_AFTER else "warning"
                uncollected = tx.execute("SELECT count(*) AS n FROM rig_files WHERE rig = ? AND state IN ('seen', 'stuck')",
                                         (self.rig,)).fetchone()["n"]
                raise_issue(tx, self.config, kind="RIG_UNREACHABLE", severity=severity, fingerprint=f"RIG_UNREACHABLE:{self.rig}",
                            message=f"Rig {self.rig} ({self.cfg.raw_root}) has been unreachable since {since}. {uncollected} known "
                                    "file(s) are not collected yet and exist only on the rig. Collection resumes when it is back.",
                            scope={"rig": self.rig})

    def _reachable(self) -> None:
        if self.catalog.get_state(f"rig_unreachable_since:{self.rig}") is not None:
            self.catalog.set_state(f"rig_unreachable_since:{self.rig}", None)
        with self.catalog.transaction() as tx:
            blobs.ensure_location(tx, self.location)
            tx.execute("UPDATE locations SET reachable = 1, last_probe_at = ? WHERE name = ?", (now_iso(), self.location))
            resolve_issue(tx, self.config, f"RIG_UNREACHABLE:{self.rig}", resolution="auto:reachable")

    # ── scan ─────────────────────────────────────────────────────────────
    def _scan(self, now: datetime, report: PollReport) -> dict[str, tuple[int, float]]:
        found: dict[str, tuple[int, float]] = {}
        root = self.source.root
        for path in root.rglob("*"):
            try:
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                st = path.stat()
            except OSError:
                continue
            if rel.endswith(".partial"):
                continue
            found[rel] = (st.st_size, st.st_mtime)
        known = {r["rel_path"]: r for r in self.catalog.query("SELECT * FROM rig_files WHERE rig = ?", (self.rig,))}
        stamp = now.isoformat()
        with self.catalog.transaction() as tx:
            for rel, (size, mtime) in found.items():
                if rel.startswith("_altair/") or matches(rel, self.cfg.session_end_marker):
                    continue
                if not selected(rel, self.cfg.include, self.cfg.exclude):
                    continue
                report.seen += 1
                row = known.get(rel)
                if row is None:
                    tx.execute("INSERT INTO rig_files(rig, rel_path, size, mtime, first_seen_at, stable_since, state) VALUES (?, ?, ?, ?, ?, ?, 'seen')",
                               (self.rig, rel, size, mtime, stamp, stamp))
                elif row["state"] in ("seen", "stuck") and (row["size"] != size or row["mtime"] != mtime):
                    tx.execute("UPDATE rig_files SET size = ?, mtime = ?, stable_since = ? WHERE rig = ? AND rel_path = ?",
                               (size, mtime, stamp, self.rig, rel))
                elif row["state"] == "gone":
                    tx.execute("UPDATE rig_files SET state = 'seen', size = ?, mtime = ?, stable_since = ? WHERE rig = ? AND rel_path = ?",
                               (size, mtime, stamp, self.rig, rel))
            for rel, row in known.items():
                if rel in found or row["state"] in ("gone", "cleaned", "excluded"):
                    continue
                report.gone += 1
                tx.execute("UPDATE rig_files SET state = 'gone' WHERE rig = ? AND rel_path = ?", (self.rig, rel))
                if row["state"] == "collected" and row["sha256"]:
                    blobs.mark_missing(tx, row["sha256"], self.location, "external_delete")
                elif row["state"] in ("seen", "stuck", "collecting"):
                    raise_issue(tx, self.config, kind="DATA_AT_RISK", severity="blocking", fingerprint=f"DATA_AT_RISK:{self.rig}:{rel}",
                                message=f"{rel} disappeared from rig {self.rig} before Altair collected it; it may have been deleted "
                                        "before any copy existed. Check the rig PC.",
                                scope={"rig": self.rig, "rig_path": rel})
        return found

    # ── session-end markers (standalone, SPEC §4.2) ──────────────────────
    def _markers(self, scanned: dict[str, tuple[int, float]]) -> None:
        for rel in scanned:
            if not matches(rel, self.cfg.session_end_marker):
                continue
            key = f"marker:{self.rig}:{rel}"
            if self.catalog.get_state(key):
                continue
            path = self.source.path(rel)
            try:
                marker = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marker = {}
            at = marker.get("at") or datetime.fromtimestamp(scanned[rel][1], timezone.utc).isoformat()
            night = marker.get("night")
            if not night:
                when = parse_date_obs(str(at)[:19]) or datetime.fromtimestamp(scanned[rel][1], timezone.utc)
                night = str(night_of(when, self.config.site.timezone, self.config.site.session_rollover_local))
            nights.request_close(self.catalog, self.config, rig=self.rig, night=night, closed_by="session_end_marker", at=at)
            self.catalog.set_state(key, {"night": night, "at": at})

    # ── collection ───────────────────────────────────────────────────────
    def _collect_stable(self, now: datetime, report: PollReport) -> None:
        wait = timedelta(seconds=self.cfg.collect.stable_seconds)
        rows = [r for r in self.catalog.query("SELECT * FROM rig_files WHERE rig = ? AND state IN ('seen', 'stuck') ORDER BY rel_path",
                                              (self.rig,))
                if now - datetime.fromisoformat(r["stable_since"]) >= wait]
        if not rows:
            return
        throttle = Throttle(self.cfg.collect.bandwidth_limit_mbps)
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.collect.parallel_files)) as pool:
            for rel, outcome in zip([r["rel_path"] for r in rows], pool.map(lambda r: self._collect_one(r, throttle), rows)):
                if outcome == "collected":
                    report.collected += 1
                elif outcome == "known":
                    report.already_known += 1
                elif outcome:
                    report.failed.append(f"{rel}: {outcome}")

    def _collect_one(self, row, throttle: Throttle) -> str | None:
        rel = row["rel_path"]
        path = self.source.path(rel)
        try:
            before = path.stat()
            if (before.st_size, before.st_mtime) != (row["size"], row["mtime"]):
                return None  # changed since the scan: wait for the next poll
            if not self.exclusive_open(path):
                return None  # NINA still has it open
            if not file_checks.complete(path):
                return self._failed(row, "incomplete or not a FITS/XISF file", corrupt=False)
            self.spool.mkdir(parents=True, exist_ok=True)
            spooled = self.spool / f".{abs(hash(rel))}.partial{path.suffix.lower()}"
            with path.open("rb") as src, spooled.open("wb") as out:
                sha, size = copy_hashing(src, out, throttle=throttle)
            if self.cfg.collect.verify == "double_read":
                second = sha256_file(path)
                after = path.stat()
                if second != sha or (after.st_size, after.st_mtime) != (before.st_size, before.st_mtime):
                    remove_file(spooled)
                    return self._failed(row, "the two reads differ", corrupt=True)
            final_spool = self.spool / f"{sha}{path.suffix.lower()}"
            if final_spool.exists():
                remove_file(spooled)
            else:
                spooled.replace(final_spool)
            return self._commit(row, path, final_spool, sha, size)
        except IntegrityMismatch as exc:
            with self.catalog.transaction() as tx:
                raise_issue(tx, self.config, kind="INTEGRITY_MISMATCH", severity="warning", fingerprint=f"INTEGRITY_MISMATCH:nas:{rel}",
                            message=str(exc), scope={"rig": self.rig, "rig_path": rel})
            return self._failed(row, str(exc), corrupt=False)
        except OSError as exc:
            return self._failed(row, f"read error: {exc}", corrupt=False)

    def _commit(self, row, path: Path, spooled: Path, sha: str, size: int) -> str:
        rel = row["rel_path"]
        header = read_header(spooled)
        logical = f"raw/{self.rig}/{rel}"
        known = self.catalog.one("SELECT logical_path FROM blobs WHERE sha256 = ?", (sha,))
        if known:
            logical = known["logical_path"]   # the same file seen again (e.g. moved on the rig): never processed twice
        nas_uri = self.nas.write_verified(spooled, logical, sha)
        result = ingest(self.catalog, self.config, self.hub_config(), rig=self.rig, header=header, sha256=sha, size=size,
                        logical_path=logical, file_name=path.name, replicas=[("nas", nas_uri), (self.location, str(path))])
        night = str(result.fields["night"])
        with self.catalog.transaction() as tx:
            tx.execute("UPDATE rig_files SET state = 'collected', sha256 = ?, night = ?, last_error = NULL WHERE rig = ? AND rel_path = ?",
                       (sha, night, self.rig, rel))
            tx.execute("INSERT OR REPLACE INTO spool_files(sha256, path, created_at) VALUES (?, ?, ?)", (sha, str(spooled), now_iso()))
            resolve_issue(tx, self.config, f"COLLECTION_CORRUPT:{self.rig}:{rel}", resolution="auto:double_read_matched")
            resolve_issue(tx, self.config, f"COLLECTION_STUCK:{self.rig}:{rel}", resolution="auto:collected")
        state = self.catalog.one("SELECT state FROM collections WHERE rig = ? AND night = ?", (self.rig, night))
        if result.created and state and state["state"] == "closed":
            self._close(night, reclose=True)   # a late frame (SPEC §7.3)
        return "collected" if result.created else "known"

    def _failed(self, row, error: str, *, corrupt: bool) -> str:
        rel = row["rel_path"]
        attempts = (row["attempts"] or 0) + 1
        with self.catalog.transaction() as tx:
            tx.execute("UPDATE rig_files SET state = 'stuck', attempts = ?, last_error = ? WHERE rig = ? AND rel_path = ?",
                       (attempts, error, self.rig, rel))
            if corrupt and attempts >= MAX_READ_MISMATCHES:
                raise_issue(tx, self.config, kind="COLLECTION_CORRUPT", severity="blocking", fingerprint=f"COLLECTION_CORRUPT:{self.rig}:{rel}",
                            message=f"{rel} on rig {self.rig} read differently {attempts} times in a row ({error}). "
                                    "Check the rig's disk and network; the file stays on the rig.",
                            scope={"rig": self.rig, "rig_path": rel})
        return error

    # ── closing nights ───────────────────────────────────────────────────
    def _finish_closing(self, now: datetime, report: PollReport) -> None:
        for row in self.catalog.query("SELECT * FROM collections WHERE rig = ? AND state = 'closing'", (self.rig,)):
            night = row["night"]
            pending = self.catalog.query("SELECT rel_path, first_seen_at, last_error FROM rig_files WHERE rig = ? AND state IN ('seen', 'stuck', 'collecting')",
                                         (self.rig,))
            if pending:
                self._stuck(now, night, pending)
                continue
            self._close(night)
            report.closed.append(night)

    def _stuck(self, now: datetime, night: str, pending) -> None:
        limit = timedelta(minutes=self.cfg.collect.stuck_minutes)
        stuck = [r for r in pending if now - datetime.fromisoformat(r["first_seen_at"]) >= limit]
        if not stuck:
            return
        with self.catalog.transaction() as tx:
            raise_issue(tx, self.config, kind="COLLECTION_STUCK", severity="blocking", fingerprint=f"COLLECTION_STUCK:{self.rig}:{night}",
                        message=f"Night {night} on rig {self.rig} can't close: {len(stuck)} file(s) stay unstable or unreadable "
                                f"(e.g. {stuck[0]['rel_path']}: {stuck[0]['last_error'] or 'still changing'}). "
                                f"Fix them, or `altair collect exclude --rig {self.rig} <path>`.",
                        scope={"rig": self.rig, "night": night, "files": [r["rel_path"] for r in stuck[:50]]})

    def _close(self, night: str, *, reclose: bool = False) -> None:
        row = self.catalog.one("SELECT closed_by, session_end_at FROM collections WHERE rig = ? AND night = ?", (self.rig, night))
        closed_by = (row["closed_by"] if row else None) or "manual"
        sha = manifest.write(self.catalog, self.config, self.nas, self.rig, night, closed_by)
        nights.close(self.catalog, self.config, rig=self.rig, night=night, closed_by=closed_by, at=row["session_end_at"] if row else None,
                     manifest_sha256=sha, reclose=reclose)
        with self.catalog.transaction() as tx:
            resolve_issue(tx, self.config, f"COLLECTION_STUCK:{self.rig}:{night}", resolution="auto:closed")

    # ── by hand ──────────────────────────────────────────────────────────
    def exclude(self, rel_path: str) -> bool:
        with self.catalog.transaction() as tx:
            return tx.execute("UPDATE rig_files SET state = 'excluded' WHERE rig = ? AND rel_path = ? AND state != 'collected'",
                              (self.rig, rel_path)).rowcount == 1
