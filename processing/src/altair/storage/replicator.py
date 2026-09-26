"""The S3 replicator (SPEC §7.5): uploads each blob whose data class is in
``backup_classes`` once, in priority order (raw lights before everything
else, and regardless of the upload window), from the spool or cache when the
file is still there and otherwise from the NAS. Copy-only: it never deletes.

It also watches the backup: ``BACKUP_BEHIND`` when collected raw lights
have no verified S3 copy after ``raw_backup_sla_hours``, and ``DATA_AT_RISK``
when a raw light has no verified durable copy at all after 48 h.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.hub.reporters import enqueue_frame_patch
from altair.issues import raise_issue, resolve_issue
from altair.storage import blobs
from altair.storage.locations import COLD_CLASSES, IntegrityMismatch, S3Location

log = logging.getLogger("altair.replicator")
PRIORITY = {"raw_light": 0, "metadata": 1, "calibration_master": 2, "project_reference": 2, "night_master": 2,
            "multi_night_master": 2, "calibrated_frame": 3}


@dataclass
class ReplicationReport:
    uploaded: int = 0
    bytes: int = 0
    already_there: int = 0
    deferred: int = 0          # outside the upload window
    no_source: int = 0
    failed: list[str] = field(default_factory=list)
    unreachable: bool = False


def in_window(window: str | None, now: datetime, tz: str) -> bool:
    """``"HH:MM-HH:MM"`` in site-local time; may wrap past midnight."""
    if not window:
        return True
    start_s, end_s = window.split("-")
    local = now.astimezone(ZoneInfo(tz)).time()
    start = datetime.strptime(start_s.strip(), "%H:%M").time()
    end = datetime.strptime(end_s.strip(), "%H:%M").time()
    return start <= local <= end if start <= end else (local >= start or local <= end)


class Replicator:
    def __init__(self, catalog: Catalog, config: AltairConfig, s3: S3Location, *, clock: Callable[[], datetime] | None = None):
        self.catalog = catalog
        self.config = config
        self.s3 = s3
        self.cfg = s3.cfg
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ── the queue ────────────────────────────────────────────────────────
    def pending(self, limit: int | None = None):
        classes = [c for c in self.cfg.backup_classes if c in PRIORITY]
        if not classes:
            return []
        marks = ",".join("?" * len(classes))
        order = " ".join(f"WHEN '{c}' THEN {p}" for c, p in PRIORITY.items())
        sql = (f"SELECT b.* FROM blobs b WHERE b.data_class IN ({marks}) AND NOT EXISTS (SELECT 1 FROM replicas r WHERE r.sha256 = b.sha256 "
               f"AND r.location = 's3' AND r.state IN ('present', 'archived_cold', 'restoring', 'restored')) "
               f"ORDER BY CASE b.data_class {order} END, b.created_at, b.sha256" + (f" LIMIT {int(limit)}" if limit else ""))
        return self.catalog.query(sql, tuple(classes))

    def source_path(self, sha256: str) -> Path | None:
        """Spool, then cache, then the NAS: whatever is local and verified."""
        spooled = self.catalog.one("SELECT path FROM spool_files WHERE sha256 = ?", (sha256,))
        if spooled and Path(spooled["path"]).exists():
            return Path(spooled["path"])
        reps = blobs.replicas(self.catalog.conn, sha256)
        for location in ("cache", "nas"):
            row = reps.get(location)
            if blobs.verified(row) and Path(row["uri"]).exists():
                return Path(row["uri"])
        return None

    # ── a pass ───────────────────────────────────────────────────────────
    def run_once(self, max_items: int | None = None) -> ReplicationReport:
        from botocore.exceptions import BotoCoreError, ClientError

        report = ReplicationReport()
        now = self.clock()
        window_open = in_window(self.cfg.upload_window, now, self.config.site.timezone)
        for row in self.pending(max_items):
            if row["data_class"] != "raw_light" and not window_open:
                report.deferred += 1
                continue
            source = self.source_path(row["sha256"])
            if source is None:
                report.no_source += 1
                continue
            try:
                info = self._upload(source, row)
            except IntegrityMismatch as exc:
                report.failed.append(str(exc))
                with self.catalog.transaction() as tx:
                    raise_issue(tx, self.config, kind="INTEGRITY_MISMATCH", severity="warning", fingerprint=f"INTEGRITY_MISMATCH:s3:{row['sha256']}",
                                message=str(exc), scope={"sha256": row["sha256"], "logical_path": row["logical_path"]})
                continue
            except (ClientError, BotoCoreError, OSError) as exc:
                report.unreachable = True
                report.failed.append(f"{row['logical_path']}: {exc}")
                log.warning("S3 upload failed, stopping this pass: %s", exc)
                break
            report.uploaded += 1
            report.bytes += row["size_bytes"]
            self._record(row, info)
        self.check_backup(now)
        return report

    def _upload(self, source: Path, row):
        """Raw lights ignore the bandwidth limit, like the upload window."""
        rate = self.cfg.upload_bandwidth_limit_mbps
        bytes_per_s = rate * 125_000 if rate and row["data_class"] != "raw_light" else None
        return self.s3.upload(source, row["logical_path"], row["sha256"], row["data_class"], bytes_per_s=bytes_per_s)

    def _record(self, row, info) -> None:
        cold = info.storage_class in COLD_CLASSES
        with self.catalog.transaction() as tx:
            blobs.set_replica(tx, row["sha256"], "s3", self.s3.uri(row["logical_path"]), kind="s3",
                              state="archived_cold" if cold else "present", method="s3_checksum_sha256",
                              storage_class=info.storage_class, version_id=info.version_id)
            if row["data_class"] in ("night_master", "multi_night_master", "project_reference") and self.config.hub.enabled:
                from altair.publish.products import reenqueue_for_blob

                reenqueue_for_blob(tx, self.config, row["sha256"])   # now with its archive_uri
            frame = tx.execute("SELECT id FROM frames WHERE sha256 = ?", (row["sha256"],)).fetchone()
            if frame and self.config.hub.enabled:
                nas = tx.execute("SELECT state FROM replicas WHERE sha256 = ? AND location = 'nas'", (row["sha256"],)).fetchone()
                enqueue_frame_patch(tx, row["sha256"], {"storage": {"nas": bool(nas and nas["state"] == "present"),
                                                                    "s3": info.storage_class, "verified_at": now_iso()}})

    # ── watching the backup (SPEC §7.2) ──────────────────────────────────
    def check_backup(self, now: datetime | None = None) -> dict:
        now = now or self.clock()
        backup = self.config.storage.backup
        sla = (now - timedelta(hours=backup.raw_backup_sla_hours)).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        risk = (now - timedelta(hours=backup.data_at_risk_hours)).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        behind = self.catalog.query(
            "SELECT f.rig, f.night, count(*) AS n FROM blobs b JOIN frames f ON f.sha256 = b.sha256 WHERE b.data_class = 'raw_light' "
            "AND b.created_at < ? AND NOT EXISTS (SELECT 1 FROM replicas r WHERE r.sha256 = b.sha256 AND r.location = 's3' "
            "AND r.state IN ('present', 'archived_cold', 'restored') AND r.verified_at IS NOT NULL) GROUP BY f.rig, f.night", (sla,))
        at_risk = self.catalog.query(
            "SELECT b.sha256, b.logical_path FROM blobs b WHERE b.data_class = 'raw_light' AND b.created_at < ? AND NOT EXISTS "
            "(SELECT 1 FROM replicas r WHERE r.sha256 = b.sha256 AND r.location IN ('nas', 's3') AND r.state IN ('present', 'archived_cold', 'restored') "
            "AND r.verified_at IS NOT NULL)", (risk,))
        open_behind = {r["fingerprint"] for r in self.catalog.query("SELECT fingerprint FROM issues WHERE kind = 'BACKUP_BEHIND' AND status = 'open'")}
        with self.catalog.transaction() as tx:
            current = set()
            for r in behind:
                fp = f"BACKUP_BEHIND:{r['rig']}:{r['night']}"
                current.add(fp)
                raise_issue(tx, self.config, kind="BACKUP_BEHIND", severity="warning", fingerprint=fp,
                            message=f"{r['n']} raw light(s) of {r['rig']} night {r['night']} have no verified S3 copy after "
                                    f"{backup.raw_backup_sla_hours:g} h. Check the uplink and `altair storage backup status`.",
                            scope={"rig": r["rig"], "night": r["night"]})
            for fp in open_behind - current:
                resolve_issue(tx, self.config, fp, resolution="auto:backed_up")
            if at_risk:
                raise_issue(tx, self.config, kind="DATA_AT_RISK", severity="blocking", fingerprint="DATA_AT_RISK:no_durable_copy",
                            message=f"{len(at_risk)} raw light(s) have had no verified NAS or S3 copy for over {backup.data_at_risk_hours:g} h "
                                    f"(e.g. {at_risk[0]['logical_path']}). Nothing of theirs is cleaned up until this clears.",
                            scope={"sha256s": [r["sha256"] for r in at_risk[:200]]})
            else:
                resolve_issue(tx, self.config, "DATA_AT_RISK:no_durable_copy", resolution="auto:backed_up")
        return {"behind": len(behind), "at_risk": len(at_risk)}

    def status(self) -> list[dict]:
        """`altair storage backup status`: per data class, blobs and bytes in S3 and pending."""
        rows = self.catalog.query(
            "SELECT b.data_class, count(*) AS blobs, sum(b.size_bytes) AS bytes, sum(CASE WHEN EXISTS (SELECT 1 FROM replicas r WHERE "
            "r.sha256 = b.sha256 AND r.location = 's3' AND r.state IN ('present', 'archived_cold', 'restored')) THEN 1 ELSE 0 END) AS in_s3 "
            "FROM blobs b GROUP BY b.data_class ORDER BY b.data_class")
        return [{"class": r["data_class"], "blobs": r["blobs"], "bytes": r["bytes"] or 0, "in_s3": r["in_s3"],
                 "pending": (r["blobs"] - r["in_s3"]) if r["data_class"] in self.cfg.backup_classes else 0} for r in rows]
