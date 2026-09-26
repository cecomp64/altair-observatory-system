"""Staging (SPEC §7.7): before PixInsight starts, every input blob of a job
resolves to a path it can read.

1. Raw lights, raw calibration subs and calibrated subs with a verified NAS
   copy are read **in place** from the NAS (nothing is copied).
2. Small inputs (masters, references) come from the local cache, filled
   from the NAS on a miss.
3. Otherwise the blob is fetched into the cache from the next source by
   read priority: the rig PC's original → S3 (instant classes) → S3 cold
   (a restore first). A blob whose NAS copy was lost (not removed by a
   cleanup rule) is written back to the NAS: the canonical store heals itself.
4. Guards: more than ``max_auto_download_gb`` from S3 needs approval
   (``FETCH_APPROVAL_NEEDED``); restores over ``max_auto_restore_gb`` too
   (``RESTORE_APPROVAL_NEEDED``); a restore in progress is
   ``RESTORE_IN_PROGRESS``; no source at all is ``DATA_UNAVAILABLE``. The job
   waits (``waiting_data``) and never holds the PixInsight slot.

If the NAS is merely unreachable, jobs wait rather than pull whole nights
from S3 (``allow_s3_fallback`` overrides that).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.hashing import sha256_file
from altair.issues import raise_issue, resolve_issue
from altair.storage import blobs
from altair.storage import nas as nas_mod
from altair.storage.locations import COLD_CLASSES, IntegrityMismatch, RestoreRequired, S3Location, make_read_only, nas_location, remove_file

log = logging.getLogger("altair.stager")
IN_PLACE = {"raw_light", "raw_calibration", "calibrated_frame"}
GB = 1024 ** 3


@dataclass
class StageResult:
    ready: bool
    paths: dict[str, str] = field(default_factory=dict)
    waiting_reason: str | None = None
    downloaded_bytes: int = 0
    restores_pending: int = 0


class Stager:
    def __init__(self, catalog: Catalog, config: AltairConfig, *, s3: S3Location | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.catalog = catalog
        self.config = config
        self.s3 = s3
        self.nas = nas_location(config)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache_root = config.cache_dir / "blobs"
        self.nas_cfg = config.storage.nas

    # ── paths ────────────────────────────────────────────────────────────
    def cache_path(self, sha: str, logical_path: str) -> Path:
        return self.cache_root / sha[:2] / f"{sha}{Path(logical_path).suffix.lower()}"

    # ── staging a job ────────────────────────────────────────────────────
    def stage(self, job_id: int, inputs: list[str], *, allow_s3_fallback: bool = False) -> StageResult:
        result = StageResult(ready=False)
        health = nas_mod.last(self.catalog)
        nas_ok = health.usable if health else bool(self.nas and self.nas.reachable())
        to_fetch: list[tuple[str, dict]] = []
        for sha in inputs:
            info = blobs.blob(self.catalog.conn, sha)
            if info is None:
                return self._wait(job_id, result, f"unknown blob {sha[:12]}…")
            reps = blobs.replicas(self.catalog.conn, sha)
            path = self._local(sha, info, reps, nas_ok)
            if path:
                result.paths[sha] = path
            else:
                to_fetch.append((sha, info))
        if not to_fetch:
            result.ready = True
            self._clear_job_issues(job_id)
            return result
        plans = []
        missing = []
        for sha, info in to_fetch:
            source = self._source(sha, info, nas_ok, allow_s3_fallback)
            if source is None:
                missing.append((sha, info))
            else:
                plans.append((sha, info, source))
        if missing:
            return self._unavailable(job_id, result, missing, nas_ok)
        if not nas_ok and not allow_s3_fallback and any(src[0] == "s3" for _, _, src in plans):
            return self._wait(job_id, result, "the NAS is unreachable; waiting for it rather than fetching from S3")
        s3_hot = sum(i["size_bytes"] for _, i, src in plans if src[0] == "s3")
        cold = [(sha, i) for sha, i, src in plans if src[0] == "s3_cold"]
        cold_bytes = sum(i["size_bytes"] for _, i in cold)
        s3_cfg = self.config.storage.s3
        if s3_cfg and s3_hot + cold_bytes > s3_cfg.max_auto_download_gb * GB and not self._approved(f"FETCH_APPROVAL_NEEDED:job:{job_id}"):
            return self._approval(job_id, result, "FETCH_APPROVAL_NEEDED", s3_hot + cold_bytes, len(plans))
        if cold:
            if s3_cfg and cold_bytes > s3_cfg.restore.max_auto_restore_gb * GB and not self._approved(f"RESTORE_APPROVAL_NEEDED:job:{job_id}"):
                return self._approval(job_id, result, "RESTORE_APPROVAL_NEEDED", cold_bytes, len(cold))
            pending = self._restore(job_id, cold)
            if pending:
                result.restores_pending = pending
                return self._wait(job_id, result, f"waiting for {pending} S3 restore(s) ({s3_cfg.restore.tier})")
        for sha, info, source in plans:
            try:
                result.paths[sha] = str(self._fetch(sha, info, source))
                result.downloaded_bytes += info["size_bytes"] if source[0].startswith("s3") else 0
            except (IntegrityMismatch, OSError, RestoreRequired) as exc:
                log.warning("fetch of %s from %s failed: %s", sha[:12], source[0], exc)
                return self._wait(job_id, result, f"fetch of {info['logical_path']} failed ({exc}); retrying from another source")
        result.ready = True
        self._clear_job_issues(job_id)
        return result

    def _local(self, sha: str, info, reps: dict, nas_ok: bool) -> str | None:
        """In place on the NAS, or already in the cache."""
        nas = reps.get("nas")
        in_place = info["data_class"] in IN_PLACE and self.nas_cfg and self.nas_cfg.read_in_place and not self.nas_cfg.stage_raw_locally
        if in_place and nas_ok and blobs.verified(nas) and Path(nas["uri"]).exists():
            if self._nas_read_ok(sha, nas["uri"]):
                return nas["uri"]
        cache = reps.get("cache")
        if cache and cache["state"] == "present" and Path(cache["uri"]).exists():
            self.catalog.execute("UPDATE replicas SET last_seen_at = ? WHERE sha256 = ? AND location = 'cache'", (now_iso(), sha))
            return cache["uri"]
        return None

    def _nas_read_ok(self, sha: str, uri: str) -> bool:
        mode = self.nas_cfg.nas_read_verify
        if mode == "none" or (mode == "sample" and int(sha[:2], 16) % 20):   # sample ≈ 5%
            return True
        if sha256_file(uri) == sha:
            return True
        with self.catalog.transaction() as tx:
            blobs.mark_corrupt(tx, sha, "nas")
            raise_issue(tx, self.config, kind="INTEGRITY_MISMATCH", severity="warning", fingerprint=f"INTEGRITY_MISMATCH:nas:{sha}",
                        message=f"The NAS copy of {uri} doesn't match its SHA-256; another copy is used and the NAS copy is healed.",
                        scope={"sha256s": [sha]})
        return False

    def _source(self, sha: str, info, nas_ok: bool, allow_s3_fallback: bool) -> tuple[str, dict] | None:
        """The next readable copy by read priority: NAS → rig → S3 → S3 cold."""
        reps = blobs.replicas(self.catalog.conn, sha)
        if nas_ok and blobs.verified(reps.get("nas")) and Path(reps["nas"]["uri"]).exists():
            return ("nas", dict(reps["nas"]))
        for location, row in sorted(reps.items()):
            if location.startswith("rig:") and blobs.verified(row) and Path(row["uri"]).exists():
                return ("rig", dict(row))
            if location.startswith("external:") and blobs.verified(row) and Path(row["uri"]).exists():
                return ("external", dict(row))
        s3 = reps.get("s3")
        if self.s3 and s3 and s3["state"] in ("present", "archived_cold", "restoring", "restored"):
            cold = (s3["storage_class"] or "") in COLD_CLASSES and s3["state"] != "restored"
            return ("s3_cold" if cold else "s3", dict(s3))
        return None

    def _fetch(self, sha: str, info, source: tuple[str, dict]) -> Path:
        dest = self.cache_path(sha, info["logical_path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        kind, row = source
        if not dest.exists():
            if kind.startswith("s3"):
                self.s3.download(info["logical_path"], dest, sha)
            else:
                partial = dest.with_name(dest.name + ".partial")
                shutil.copyfile(row["uri"], partial)
                if sha256_file(partial) != sha:
                    remove_file(partial)
                    with self.catalog.transaction() as tx:
                        blobs.mark_corrupt(tx, sha, row["location"])
                    raise IntegrityMismatch(f"{row['uri']} doesn't match its SHA-256")
                os.replace(partial, dest)
                make_read_only(dest)
        with self.catalog.transaction() as tx:
            blobs.set_replica(tx, sha, "cache", str(dest))
        self._heal_nas(sha, info, dest)
        return dest

    def _heal_nas(self, sha: str, info, local: Path) -> None:
        """A NAS copy that was lost (not removed by a cleanup rule) is written
        back from the fetched copy (§7.7 step 3)."""
        if self.nas is None or not self.nas_cfg or info["data_class"] not in self.nas_cfg.stores:
            return
        nas = blobs.replicas(self.catalog.conn, sha).get("nas")
        lost = nas is not None and (nas["state"] == "corrupt" or (nas["state"] == "missing" and nas["missing_reason"] != "cleanup"))
        health = nas_mod.last(self.catalog)
        if not lost or (health and not health.usable):
            return
        target = Path(nas["uri"]) if nas["uri"] else self.nas.path(info["logical_path"])
        if target.exists():
            remove_file(target)   # the corrupt copy (content never kept once it failed its hash)
        uri = self.nas.write_verified(local, info["logical_path"], sha)
        with self.catalog.transaction() as tx:
            blobs.set_replica(tx, sha, "nas", uri)
            resolve_issue(tx, self.config, f"NAS_FILE_MISSING:{sha}", resolution="auto:healed")
            resolve_issue(tx, self.config, f"INTEGRITY_MISMATCH:nas:{sha}", resolution="auto:healed")

    # ── restores (§7.7 step 4) ───────────────────────────────────────────
    def _restore(self, job_id: int, cold: list[tuple[str, dict]]) -> int:
        """Request restores (batched per job); returns how many are still pending."""
        pending = 0
        for sha, info in cold:
            request = self.catalog.one("SELECT * FROM fetch_requests WHERE sha256 = ? AND state IN ('restoring', 'pending')", (sha,))
            head = self.s3.head(info["logical_path"])
            if head and head.restore == "done":
                with self.catalog.transaction() as tx:
                    tx.execute("UPDATE replicas SET state = 'restored' WHERE sha256 = ? AND location = 's3'", (sha,))
                    tx.execute("UPDATE fetch_requests SET state = 'done', updated_at = ? WHERE sha256 = ? AND state = 'restoring'", (now_iso(), sha))
                continue
            pending += 1
            if request is None:
                self.s3.request_restore(info["logical_path"])
                with self.catalog.transaction() as tx:
                    tx.execute("INSERT INTO fetch_requests(job_id, sha256, source_location, state, updated_at) VALUES (?, ?, 's3', 'restoring', ?)",
                               (job_id, sha, now_iso()))
                    tx.execute("UPDATE replicas SET state = 'restoring' WHERE sha256 = ? AND location = 's3'", (sha,))
        with self.catalog.transaction() as tx:
            fingerprint = f"RESTORE_IN_PROGRESS:job:{job_id}"
            if pending:
                cfg = self.config.storage.s3.restore
                eta = {"Bulk": "up to 48 h", "Standard": "up to 12 h", "Expedited": "minutes"}[cfg.tier]
                raise_issue(tx, self.config, kind="RESTORE_IN_PROGRESS", severity="info", fingerprint=fingerprint,
                            message=f"Job {job_id} waits for {pending} object(s) to come back from S3 cold storage ({cfg.tier}, {eta}).",
                            scope={"job_id": job_id})
            else:
                resolve_issue(tx, self.config, fingerprint, resolution="auto:restored")
        return pending

    def poll_restores(self) -> int:
        """The every-30-minutes restore poller: jobs whose restores finished can stage again."""
        done = 0
        for req in self.catalog.query("SELECT f.*, b.logical_path FROM fetch_requests f JOIN blobs b USING (sha256) WHERE f.state = 'restoring'"):
            head = self.s3.head(req["logical_path"]) if self.s3 else None
            if head and head.restore == "done":
                with self.catalog.transaction() as tx:
                    tx.execute("UPDATE fetch_requests SET state = 'done', updated_at = ? WHERE id = ?", (now_iso(), req["id"]))
                    tx.execute("UPDATE replicas SET state = 'restored' WHERE sha256 = ? AND location = 's3'", (req["sha256"],))
                    tx.execute("UPDATE jobs SET status = 'queued', waiting_reason = NULL WHERE id = ? AND status = 'waiting_data'", (req["job_id"],))
                done += 1
        return done

    # ── waiting ──────────────────────────────────────────────────────────
    def _wait(self, job_id: int, result: StageResult, reason: str) -> StageResult:
        result.waiting_reason = reason
        return result

    def _approved(self, fingerprint: str) -> bool:
        return self.catalog.get_state(f"fetch_decision:{fingerprint}") == "approved"

    def _approval(self, job_id: int, result: StageResult, kind: str, size: int, count: int) -> StageResult:
        fingerprint = f"{kind}:job:{job_id}"
        if self.catalog.get_state(f"fetch_decision:{fingerprint}") == "denied":
            return self._wait(job_id, result, "denied")
        job = self.catalog.one("SELECT kind, scope_json FROM jobs WHERE id = ?", (job_id,))
        scope = json.loads(job["scope_json"]) if job else {}
        what = "download" if kind == "FETCH_APPROVAL_NEEDED" else "restore"
        with self.catalog.transaction() as tx:
            raise_issue(tx, self.config, kind=kind, severity="blocking", fingerprint=fingerprint,
                        message=f"Job {job_id} ({job['kind'] if job else '?'} {scope.get('night', '')} {scope.get('filter') or ''}) needs to "
                                f"{what} {count} object(s), {size / GB:.1f} GB, from S3. Approve with `altair storage approve {fingerprint}` "
                                "or in the Hub.",
                        scope={**scope, "job_id": job_id, "bytes": size})
        return self._wait(job_id, result, f"{kind}: approval needed")

    def _unavailable(self, job_id: int, result: StageResult, missing, nas_ok: bool) -> StageResult:
        where = []
        for sha, info in missing[:5]:
            locs = [f"{r['location']} ({r['state']})" for r in blobs.replicas(self.catalog.conn, sha).values()]
            where.append(f"{info['logical_path']}: {', '.join(locs) or 'no known copy'}")
        with self.catalog.transaction() as tx:
            raise_issue(tx, self.config, kind="DATA_UNAVAILABLE", severity="blocking", fingerprint=f"DATA_UNAVAILABLE:job:{job_id}",
                        message=f"Job {job_id} can't read {len(missing)} input(s) from any location"
                                + ("" if nas_ok else " (the NAS is unreachable)") + ": " + "; ".join(where)
                                + ". It re-queues when a copy becomes reachable.",
                        scope={"job_id": job_id, "sha256s": [sha for sha, _ in missing]})
        return self._wait(job_id, result, "DATA_UNAVAILABLE")

    def _clear_job_issues(self, job_id: int) -> None:
        with self.catalog.transaction() as tx:
            for kind in ("DATA_UNAVAILABLE", "FETCH_APPROVAL_NEEDED", "RESTORE_APPROVAL_NEEDED", "RESTORE_IN_PROGRESS"):
                resolve_issue(tx, self.config, f"{kind}:job:{job_id}", resolution="auto:staged")

    # ── cache eviction (§7.7) ────────────────────────────────────────────
    def evict(self) -> dict:
        """LRU down to ``max_size_gb`` and ``min_free_gb``. Never pinned
        classes, inputs of queued or running jobs, or a blob whose only
        verified copy is the cache."""
        cfg = self.config.storage.cache
        rows = self.catalog.query("SELECT r.sha256, r.uri, r.last_seen_at, b.data_class, b.size_bytes FROM replicas r JOIN blobs b USING (sha256) "
                                  "WHERE r.location = 'cache' AND r.state = 'present' ORDER BY r.last_seen_at")
        total = sum(r["size_bytes"] for r in rows)
        free = shutil.disk_usage(self.config.cache_dir).free if self.config.cache_dir.exists() else None
        need_size = total - cfg.max_size_gb * GB
        need_free = (cfg.min_free_gb * GB - free) if free is not None else 0
        to_free = max(need_size, need_free, 0)
        if to_free <= 0:
            return {"freed": 0}
        busy = self._job_inputs()
        freed = 0
        for r in rows:
            if freed >= to_free:
                break
            if r["data_class"] in cfg.pin or r["sha256"] in busy or not blobs.verified_locations(self.catalog.conn, r["sha256"], exclude=["cache"]):
                continue
            remove_file(Path(r["uri"]))
            with self.catalog.transaction() as tx:
                tx.execute("DELETE FROM replicas WHERE sha256 = ? AND location = 'cache'", (r["sha256"],))
            freed += r["size_bytes"]
        with self.catalog.transaction() as tx:
            if freed < to_free:
                raise_issue(tx, self.config, kind="CACHE_FULL", severity="blocking", fingerprint="CACHE_FULL",
                            message=f"The cache can't make room: {(to_free - freed) / GB:.1f} GB more is needed but everything left is pinned, "
                                    "in use, or the only copy. Free space or raise storage.cache.max_size_gb.", scope={"location": "cache"})
            else:
                resolve_issue(tx, self.config, "CACHE_FULL", resolution="auto:evicted")
        return {"freed": freed}

    def _job_inputs(self) -> set[str]:
        busy: set[str] = set()
        for job in self.catalog.query("SELECT plan_json FROM jobs WHERE status IN ('queued', 'staging', 'waiting_data', 'running')"):
            busy |= set(json.loads(job["plan_json"]).get("input_paths", {}))
        return busy

    # ── repopulating a replaced NAS (§7.10) ──────────────────────────────
    def replicate_to_nas(self, classes: list[str], *, dry_run: bool = False) -> dict:
        """Write every blob of ``classes`` whose NAS copy is missing to the NAS,
        from the cache or S3 (downloaded and verified first)."""
        if self.nas is None:
            raise ValueError("no NAS configured")
        marks = ",".join("?" * len(classes))
        out = {"written": 0, "bytes": 0, "present": 0, "no_source": 0, "planned": []}
        for row in self.catalog.query(f"SELECT * FROM blobs WHERE data_class IN ({marks}) ORDER BY logical_path", tuple(classes)):
            target = self.nas.path(row["logical_path"])
            if target.exists():
                out["present"] += 1
                continue
            reps = blobs.replicas(self.catalog.conn, row["sha256"])
            cache = reps.get("cache")
            local = Path(cache["uri"]) if blobs.verified(cache) and Path(cache["uri"]).exists() else None
            source = "cache" if local else ("s3" if self.s3 and reps.get("s3") and reps["s3"]["state"] in ("present", "restored") else None)
            if source is None:
                out["no_source"] += 1
                continue
            out["planned"].append((row["logical_path"], source, row["size_bytes"]))
            if dry_run:
                continue
            if local is None:
                local = self._fetch(row["sha256"], row, ("s3", dict(reps["s3"])))
            uri = self.nas.write_verified(local, row["logical_path"], row["sha256"])
            with self.catalog.transaction() as tx:
                blobs.set_replica(tx, row["sha256"], "nas", uri)
            out["written"] += 1
            out["bytes"] += row["size_bytes"]
        return out

    # ── hand-off (§7.7 step 7) ───────────────────────────────────────────
    def handoff(self, work_dir: Path, paths: dict[str, str], names: dict[str, str]) -> dict[str, str]:
        """NAS paths stay as they are; cache files are hard-linked into
        ``<work>/inputs/`` under their original names (copied if linking fails)."""
        inputs = work_dir / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        out = {}
        for sha, path in paths.items():
            if not str(path).startswith(str(self.cache_root)):
                out[sha] = path
                continue
            name = names.get(sha) or Path(path).name
            target = inputs / name
            if not target.exists():
                try:
                    os.link(path, target)
                except OSError:
                    shutil.copyfile(path, target)
            out[sha] = str(target)
        return out
