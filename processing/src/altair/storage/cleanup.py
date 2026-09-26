"""Retention and cleanup (SPEC §7.6): the only code in Altair that deletes
files.

Every rule is per location, and a deletion in one location never causes one
anywhere else. Before each delete the required backup is re-checked (a fresh
S3 HeadObject, a file-system stat, or a re-hash) and the blob must keep at
least one other verified copy: Altair never deletes the only verified copy
of anything. Each deletion (and each planned one, in a dry run) is written
to ``cleanup_ledger`` with the copies it relied on.

Nothing is cleaned anywhere while the NAS is unreachable or unhealthy, or
while ``S3_CONFIG_UNSAFE`` is open.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from altair.catalog.db import Catalog, now_iso
from altair.collector.patterns import matches
from altair.config import AltairConfig, CleanupRule
from altair.hub.reporters import enqueue_frame_patch
from altair.storage import blobs
from altair.storage import nas as nas_mod
from altair.storage.locations import FsLocation, IntegrityMismatch, S3Location, nas_location, remove_file, rig_location

log = logging.getLogger("altair.cleanup")


@dataclass
class Candidate:
    location: str
    uri: str
    rule: str
    sha256: str | None = None
    bytes: int = 0
    logical_path: str | None = None
    rel_path: str | None = None      # rig files
    version_id: str | None = None    # S3


@dataclass
class CleanupReport:
    dry_run: bool
    deleted: list[Candidate] = field(default_factory=list)
    skipped: list[tuple[Candidate, str]] = field(default_factory=list)
    blocked: str | None = None

    @property
    def freed_bytes(self) -> int:
        return sum(c.bytes for c in self.deleted)


def _days_ago(now: datetime, days: float) -> str:
    return (now - timedelta(days=days)).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Cleaner:
    def __init__(self, catalog: Catalog, config: AltairConfig, *, s3: S3Location | None = None, clock: Callable[[], datetime] | None = None,
                 rig_hash: Callable[[Path], str] | None = None):
        self.catalog = catalog
        self.config = config
        self.nas: FsLocation | None = nas_location(config)
        self.s3 = s3
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        from altair.hashing import sha256_file

        self.rig_hash = rig_hash or sha256_file

    # ── entry point ──────────────────────────────────────────────────────
    def run(self, *, dry_run: bool | None = None, location: str | None = None) -> CleanupReport:
        dry = self.config.storage.cleanup.dry_run if dry_run is None else dry_run
        report = CleanupReport(dry_run=dry)
        blocked = self._blocked()
        if blocked:
            report.blocked = blocked
            return report
        now = self.clock()
        for candidate in self.plan(now, location):
            relied_on, why = self._recheck(candidate)
            if why:
                report.skipped.append((candidate, why))
                continue
            if not dry:
                try:
                    self._delete(candidate)
                except OSError as exc:
                    report.skipped.append((candidate, f"delete failed: {exc}"))
                    continue
            self._record(candidate, relied_on, dry)
            report.deleted.append(candidate)
        if not dry:
            self._remove_empty_rig_dirs()
        return report

    def _blocked(self) -> str | None:
        if self.nas is not None:
            health = nas_mod.check(self.catalog, self.config, self.nas)
            if not health.reachable:
                return "the NAS is unreachable"
            if not health.healthy:
                return f"the NAS is unhealthy ({health.reason})"
        if self.catalog.one("SELECT 1 FROM issues WHERE kind = 'S3_CONFIG_UNSAFE' AND status = 'open'"):
            return "S3_CONFIG_UNSAFE is open"
        return None

    # ── planning ─────────────────────────────────────────────────────────
    def plan(self, now: datetime | None = None, location: str | None = None) -> list[Candidate]:
        now = now or self.clock()
        out: list[Candidate] = []
        if location in (None, "spool"):
            out += self._plan_spool()
        for rig in self.config.rigs:
            if location in (None, f"rig:{rig}", "rig"):
                out += self._plan_rig(rig, now)
        if location in (None, "nas") and self.nas:
            out += self._plan_nas(now)
        if location in (None, "s3") and self.s3:
            out += self._plan_s3(now)
        return out

    def _plan_spool(self) -> list[Candidate]:
        s3_classes = set(self.config.storage.s3.backup_classes) if self.config.storage.s3 else set()
        out = []
        for row in self.catalog.query("SELECT s.sha256, s.path, b.data_class, b.size_bytes FROM spool_files s JOIN blobs b USING (sha256)"):
            reps = blobs.replicas(self.catalog.conn, row["sha256"])
            if not blobs.verified(reps.get("nas")):
                continue
            if row["data_class"] in s3_classes and self.s3 and not blobs.verified(reps.get("s3")):
                continue   # the upload still reads from the spool
            out.append(Candidate("spool", row["path"], "spool", row["sha256"], row["size_bytes"]))
        return out

    def _plan_rig(self, rig: str, now: datetime) -> list[Candidate]:
        rules = self.config.rig_cleanup(rig)
        source = rig_location(self.config, rig)
        if not rules.enabled or source is None or not source.reachable():
            return []
        out: list[Candidate] = []
        free = source.free_percent()
        pressure = free is not None and free < rules.target_free_percent
        rows = self.catalog.query(
            "SELECT rf.rel_path, rf.size, rf.mtime, rf.sha256, b.data_class, b.size_bytes FROM rig_files rf JOIN blobs b ON b.sha256 = rf.sha256 "
            "WHERE rf.rig = ? AND rf.state = 'collected' ORDER BY rf.mtime", (rig,))
        for row in rows:
            rule: CleanupRule | None = getattr(rules, row["data_class"], None)
            if rule is None or row["rel_path"] is None or any(matches(row["rel_path"], k) for k in rules.keep):
                continue
            old_enough = rule.min_age_days is not None and row["mtime"] <= (now - timedelta(days=rule.min_age_days)).timestamp()
            if not old_enough and not pressure:
                continue
            out.append(Candidate(f"rig:{rig}", str(source.path(row["rel_path"])), f"rig:{rig}.{row['data_class']}", row["sha256"],
                                 row["size_bytes"], rel_path=row["rel_path"]))
        # Old session-end markers (they are no blobs).
        keep = rules.markers_keep_days
        marker_dir = source.root / "_altair"
        if marker_dir.is_dir():
            for path in marker_dir.iterdir():
                rel = path.relative_to(source.root).as_posix()
                if path.is_file() and matches(rel, self.config.rigs[rig].session_end_marker) and path.stat().st_mtime <= (now - timedelta(days=keep)).timestamp():
                    out.append(Candidate(f"rig:{rig}", str(path), f"rig:{rig}.session_end_marker", bytes=path.stat().st_size, rel_path=rel))
        return out

    def _plan_nas(self, now: datetime) -> list[Candidate]:
        cfg = self.config.storage.cleanup.nas
        free = self.nas.free_percent()
        pressure = free is not None and free < cfg.target_free_percent
        out: list[Candidate] = []
        for data_class in ("raw_light", "calibrated_frame"):
            rule: CleanupRule = getattr(cfg, data_class)
            if rule.min_age_days is None:
                continue
            cutoff = _days_ago(now, 30 if pressure else rule.min_age_days)
            rows = self.catalog.query(
                "SELECT b.*, r.uri FROM blobs b JOIN replicas r ON r.sha256 = b.sha256 AND r.location = 'nas' AND r.state = 'present' "
                "WHERE b.data_class = ? AND b.created_at <= ? ORDER BY b.created_at", (data_class, cutoff))
            for row in rows:
                if self._nas_requirements_hold(row, rule, now):
                    out.append(Candidate("nas", row["uri"], f"nas.{data_class}", row["sha256"], row["size_bytes"], row["logical_path"]))
        rule = cfg.raw_calibration
        if rule.min_age_days is not None:
            out += self._plan_nas_raw_calibration(rule, now)
        return out

    def _nas_requirements_hold(self, row, rule: CleanupRule, now: datetime) -> bool:
        sha = row["sha256"]
        frame = self.catalog.one("SELECT * FROM frames WHERE sha256 = ?", (sha,))
        project = self._project_for(frame) if frame else self._project_for_calibrated(row["logical_path"])
        for requirement in rule.require:
            if requirement.startswith("s3_verified") and not blobs.verified(blobs.replicas(self.catalog.conn, sha).get("s3")):
                return False
            if requirement == "night_processed" and frame is not None and frame["status"] not in ("processed", "rejected"):
                return False
            if requirement == "no_open_issue" and self._has_open_issue(sha, frame):
                return False
            if requirement.startswith("project_idle_days_") and project is not None:
                idle = float(requirement.rsplit("_", 1)[1])
                last = self.catalog.one("SELECT max(f.night) AS night FROM frames f WHERE f.image_type = 'light' AND "
                                        + ("f.hub_target_id = ? AND f.rig = ?" if project["hub_target_id"] else "f.target = ? AND f.rig = ?"),
                                        (project["hub_target_id"] or project["target"], project["rig"]))
                if last and last["night"] and last["night"] > (now - timedelta(days=idle)).date().isoformat():
                    return False
            if requirement == "not_frame_reintegration_project" and project is not None and project["multi_night_mode"] == "frame_reintegration":
                return False
        if project is not None and self._pinned(project):
            return False
        return True

    def _plan_nas_raw_calibration(self, rule: CleanupRule, now: datetime) -> list[Candidate]:
        cutoff = _days_ago(now, rule.min_age_days)
        out = []
        for master in self.catalog.query("SELECT cm.sha256, cm.source_frames_json, b.created_at FROM calibration_masters cm "
                                         "JOIN blobs b ON b.sha256 = cm.sha256 WHERE b.created_at <= ?", (cutoff,)):
            reps = blobs.replicas(self.catalog.conn, master["sha256"])
            if not (blobs.verified(reps.get("nas")) and blobs.verified(reps.get("s3"))):
                continue
            for sub in json.loads(master["source_frames_json"] or "[]"):
                row = self.catalog.one("SELECT b.*, r.uri FROM blobs b JOIN replicas r ON r.sha256 = b.sha256 AND r.location = 'nas' "
                                       "AND r.state = 'present' WHERE b.sha256 = ? AND b.data_class = 'raw_calibration'", (sub,))
                if row and not self._has_open_issue(sub, self.catalog.one("SELECT * FROM frames WHERE sha256 = ?", (sub,))):
                    out.append(Candidate("nas", row["uri"], "nas.raw_calibration", sub, row["size_bytes"], row["logical_path"]))
        return out

    def _plan_s3(self, now: datetime) -> list[Candidate]:
        keep = self.config.storage.cleanup.s3
        out: list[Candidate] = []
        # Superseded multi-night versions beyond the newest N (regenerable from night masters).
        for group in self.catalog.query("SELECT project_id, filter FROM multi_night_masters GROUP BY project_id, filter"):
            rows = self.catalog.query("SELECT m.sha256, b.logical_path, b.size_bytes, r.version_id FROM multi_night_masters m "
                                      "JOIN blobs b ON b.sha256 = m.sha256 JOIN replicas r ON r.sha256 = m.sha256 AND r.location = 's3' "
                                      "AND r.state = 'present' WHERE m.project_id = ? AND m.filter = ? ORDER BY m.version DESC",
                                      (group["project_id"], group["filter"]))
            for row in rows[keep.multi_night_master_versions_keep:]:
                out.append(Candidate("s3", self.s3.uri(row["logical_path"]), "s3.multi_night_master", row["sha256"], row["size_bytes"],
                                     row["logical_path"], version_id=row["version_id"]))
        # Catalog backups: the newest `daily` of them, plus the newest of each of the last `monthly` months.
        backups = self.catalog.query("SELECT b.sha256, b.logical_path, b.size_bytes, b.created_at, r.version_id FROM blobs b JOIN replicas r "
                                     "ON r.sha256 = b.sha256 AND r.location = 's3' AND r.state = 'present' WHERE b.data_class = 'metadata' "
                                     "AND b.logical_path LIKE 'catalog/%' ORDER BY b.created_at DESC")
        kept = {r["sha256"] for r in backups[:keep.catalog_backups_keep.daily]}
        months: dict[str, str] = {}
        for r in backups:
            months.setdefault(r["created_at"][:7], r["sha256"])
        kept |= set(list(months.values())[:keep.catalog_backups_keep.monthly])
        for r in backups:
            if r["sha256"] not in kept:
                out.append(Candidate("s3", self.s3.uri(r["logical_path"]), "s3.catalog_backup", r["sha256"], r["size_bytes"],
                                     r["logical_path"], version_id=r["version_id"]))
        return out

    # ── helpers ──────────────────────────────────────────────────────────
    def _project_for(self, frame):
        if frame["hub_target_id"] is not None:
            return self.catalog.one("SELECT * FROM projects WHERE hub_target_id = ? AND rig = ?", (frame["hub_target_id"], frame["rig"]))
        return self.catalog.one("SELECT * FROM projects WHERE target = ? AND rig = ?", (frame["target"], frame["rig"]))

    def _project_for_calibrated(self, logical_path: str):
        parts = logical_path.split("/")
        if len(parts) > 2 and parts[0] == "projects":
            return self.catalog.one("SELECT * FROM projects WHERE rig = ? AND ? LIKE 'projects/' || rig || '/T' || hub_target_id || '_%'",
                                    (parts[1], logical_path))
        return None

    def _pinned(self, project) -> bool:
        pins = set(self.config.storage.cleanup.nas.pin_projects)
        return bool(pins & {project["target"], str(project["hub_target_id"]), f"T{project['hub_target_id']}"})

    def _has_open_issue(self, sha: str, frame) -> bool:
        for issue in self.catalog.query("SELECT scope_json FROM issues WHERE status = 'open'"):
            scope = json.loads(issue["scope_json"] or "{}")
            if sha in scope.get("sha256s", []):
                return True
            if frame is not None:
                if frame["id"] in scope.get("frame_ids", []):
                    return True
                if scope.get("rig") == frame["rig"] and scope.get("night") == frame["night"] and scope.get("filter") in (None, frame["filter"]):
                    return True
        return False

    # ── the re-check right before deleting ──────────────────────────────
    def _recheck(self, c: Candidate) -> tuple[list[dict], str | None]:
        """Returns (the copies relied on, a reason to skip)."""
        if c.sha256 is None:
            return [], None   # markers, published copies: no blob
        if self.catalog.one("SELECT 1 FROM issues WHERE kind = 'DATA_AT_RISK' AND status = 'open' AND scope_json LIKE ?", (f"%{c.sha256}%",)):
            return [], "the blob is DATA_AT_RISK"
        reps = blobs.replicas(self.catalog.conn, c.sha256)
        others = {loc: row for loc, row in reps.items() if loc != c.location and blobs.verified(row)}
        relied: list[dict] = []
        if c.location.startswith("rig:"):
            need = ["nas", "s3"] if c.rule.endswith(".raw_light") else ["nas"]
            for loc in need:
                ok = self._fresh(loc, c.sha256, reps.get(loc))
                if not ok:
                    return [], f"no fresh verified {loc} copy"
                relied.append(ok)
            rig_file = Path(c.uri)
            row = self.catalog.one("SELECT size, mtime FROM rig_files WHERE rig = ? AND rel_path = ?", (c.location[4:], c.rel_path))
            try:
                st = rig_file.stat()
            except FileNotFoundError:
                return [], "already gone from the rig"
            if row is None or (st.st_size, st.st_mtime) != (row["size"], row["mtime"]) or self.rig_hash(rig_file) != c.sha256:
                return [], "the rig file changed since it was collected"
        elif c.location == "nas":
            s3 = self._fresh("s3", c.sha256, reps.get("s3"))
            if not s3:
                return [], "no fresh verified S3 copy"
            relied.append(s3)
            if c.rule == "nas.raw_calibration":
                pass   # not in S3 by design; its masters are (checked when planned)
            else:
                try:
                    if self.nas.sha256(c.uri) != c.sha256:
                        with self.catalog.transaction() as tx:
                            blobs.mark_corrupt(tx, c.sha256, "nas")
                        return [], "the NAS copy is corrupt (it is healed from S3 first)"
                except OSError:
                    return [], "the NAS copy is unreadable"
        elif c.location == "spool":
            nas = self._fresh("nas", c.sha256, reps.get("nas"))
            if not nas:
                return [], "no verified NAS copy"
            relied.append(nas)
        elif c.location == "s3":
            relied = [{"location": loc, "uri": row["uri"]} for loc, row in others.items()]
            if c.rule == "s3.multi_night_master":
                pass   # regenerable from the backed-up night masters
            elif not others:
                return [], "it would remove the only verified copy"
        if not others and c.location != "s3":
            return [], "it would remove the only verified copy"
        return relied, None

    def _fresh(self, location: str, sha: str, row) -> dict | None:
        if not blobs.verified(row):
            return None
        if location == "s3":
            if self.s3 is None:
                return None
            blob = blobs.blob(self.catalog.conn, sha)
            info = self.s3.verify_fresh(blob["logical_path"], sha, blob["size_bytes"])
            if info is None or (row["version_id"] and info.version_id and info.version_id != row["version_id"]):
                return None
            if self.config.storage.s3.object_lock and blob["data_class"] in self.config.storage.s3.object_lock.classes and info.retain_until is None:
                return None
            return {"location": "s3", "uri": row["uri"], "version_id": info.version_id, "checked": "head_object"}
        path = Path(row["uri"])
        try:
            if path.stat().st_size != blobs.blob(self.catalog.conn, sha)["size_bytes"]:
                return None
        except OSError:
            return None
        return {"location": location, "uri": row["uri"], "checked": "stat"}

    # ── deleting ─────────────────────────────────────────────────────────
    def _delete(self, c: Candidate) -> None:
        if c.location == "s3":
            self.s3.delete(c.logical_path, c.version_id)
        else:
            remove_file(Path(c.uri))
            if Path(c.uri).exists():
                raise OSError(f"{c.uri} could not be deleted")

    def _record(self, c: Candidate, relied_on: list[dict], dry: bool) -> None:
        with self.catalog.transaction() as tx:
            tx.execute("INSERT INTO cleanup_ledger(sha256, location, uri, rule, relied_on_json, bytes, deleted_at, dry_run) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (c.sha256, c.location, c.uri, c.rule, json.dumps(relied_on), c.bytes, now_iso(), int(dry)))
            if dry:
                return
            if c.location == "spool":
                tx.execute("DELETE FROM spool_files WHERE sha256 = ?", (c.sha256,))
                return
            if c.sha256:
                blobs.mark_missing(tx, c.sha256, c.location, "cleanup")
            if c.location.startswith("rig:") and c.rel_path:
                tx.execute("UPDATE rig_files SET state = 'cleaned' WHERE rig = ? AND rel_path = ?", (c.location[4:], c.rel_path))
            if c.location == "nas" and self.config.hub.enabled and c.sha256:
                frame = tx.execute("SELECT id FROM frames WHERE sha256 = ?", (c.sha256,)).fetchone()
                if frame:
                    s3 = tx.execute("SELECT storage_class FROM replicas WHERE sha256 = ? AND location = 's3'", (c.sha256,)).fetchone()
                    enqueue_frame_patch(tx, c.sha256, {"storage": {"nas": False, "s3": s3["storage_class"] if s3 else None}})

    def _remove_empty_rig_dirs(self) -> None:
        """Empty folders under a rig's raw root, never the root, ``_altair``,
        or a folder touched in the last hour (NINA may be writing there)."""
        cutoff = self.clock().timestamp() - 3600
        for rig in self.config.rigs:
            source = rig_location(self.config, rig)
            if source is None or not source.reachable():
                continue
            for dirpath, dirnames, filenames in os.walk(source.root, topdown=False):
                path = Path(dirpath)
                if path == source.root or "_altair" in path.relative_to(source.root).parts:
                    continue
                try:
                    if not any(path.iterdir()) and path.stat().st_mtime < cutoff:
                        path.rmdir()
                except OSError:
                    continue


def ledger(catalog: Catalog, since: str | None = None) -> list[dict]:
    rows = catalog.query("SELECT * FROM cleanup_ledger WHERE deleted_at >= ? ORDER BY id", (since or "",))
    return [dict(r) for r in rows]


__all__ = ["Cleaner", "CleanupReport", "Candidate", "ledger", "IntegrityMismatch"]
