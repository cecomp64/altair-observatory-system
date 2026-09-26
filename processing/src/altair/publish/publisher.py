"""The publisher (SPEC §6.6): turns a successful PixInsight run into
catalog records and stored blobs.

Every output is registered as a blob under a logical path that carries a
version or content-hash suffix (archive keys are never overwritten, §7.1),
written to the NAS with a verified write, copied into the local cache
(pinned classes stay there) and left to the replicator for S3. Provisional
no-flat masters stay in the cache and the published folder only: they are
never merged and never archived.

Publishing is idempotent: blobs, replicas and master rows are keyed by
SHA-256, so publishing the same result twice (after a crash, §6.5) changes
nothing.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.executor.contract import Output, RunResult
from altair.hashing import sha256_file
from altair.hub.reporters import enqueue_calibration_master, enqueue_frame_patch
from altair.planner import matching
from altair.planner.projects import project_label, settings
from altair.publish import products, verify
from altair.storage import blobs
from altair.storage import nas as nas_mod
from altair.storage.locations import make_read_only, nas_location

log = logging.getLogger("altair.publisher")
CALIB_ISSUE_KINDS = ("FLAT_MISSING", "DARK_MISSING", "BIAS_MISSING", "DARKFLAT_MISSING", "ROTATOR_POSITION_UNKNOWN")


class VerifyError(Exception):
    """The run succeeded but its output fails the sanity checks; not retried."""


def _sha8(sha: str) -> str:
    return sha[:8]


def _fmt(value: float | None) -> str:
    return "na" if value is None else f"{value:g}"


class Publisher:
    def __init__(self, catalog: Catalog, config: AltairConfig, *, read_images: bool = True,
                 merge_planner: Callable[[Catalog, AltairConfig, int, str], Any] | None = None):
        self.catalog = catalog
        self.config = config
        self.nas = nas_location(config)
        self.read_images = read_images
        if merge_planner is None:
            from altair.projects.merge import plan_merge as merge_planner
        self.plan_merge = merge_planner

    # ── storing a file ───────────────────────────────────────────────────
    def cache_path(self, sha: str, logical_path: str) -> Path:
        return self.config.cache_dir / "blobs" / sha[:2] / f"{sha}{Path(logical_path).suffix.lower()}"

    def store(self, source: str | Path, logical_for: Callable[[str], str], data_class: str, *, rig: str | None = None,
              to_nas: bool = True) -> tuple[str, str, int]:
        """Hash, register, copy into the cache, write to the NAS. Returns (sha, logical path, size)."""
        source = Path(source)
        sha = sha256_file(source)
        size = source.stat().st_size
        logical = logical_for(sha)
        cached = self.cache_path(sha, logical)
        if not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            partial = cached.with_name(cached.name + ".partial")
            shutil.copyfile(source, partial)
            os.replace(partial, cached)
            make_read_only(cached)
        with self.catalog.transaction() as tx:
            blobs.add_blob(tx, sha, size, data_class, logical, rig)
            blobs.set_replica(tx, sha, "cache", str(cached))
        if to_nas:
            self._to_nas(sha, cached, logical, data_class)
        return sha, logical, size

    def _to_nas(self, sha: str, local: Path, logical: str, data_class: str) -> bool:
        cfg = self.config.storage.nas
        if self.nas is None or cfg is None or data_class not in cfg.stores:
            return False
        health = nas_mod.last(self.catalog)
        if (health and not health.usable) or not self.nas.reachable():
            log.info("NAS not usable; %s stays in the cache until `backfill_nas`", logical)
            return False
        uri = self.nas.write_verified(local, logical, sha)
        with self.catalog.transaction() as tx:
            blobs.set_replica(tx, sha, "nas", uri)
        return True

    def backfill_nas(self) -> int:
        """Outputs published while the NAS was unusable are written to it now."""
        cfg = self.config.storage.nas
        if cfg is None:
            return 0
        classes = [c for c in cfg.stores if c not in ("raw_light", "raw_calibration")]
        marks = ",".join("?" * len(classes))
        rows = self.catalog.query(
            f"SELECT b.sha256, b.logical_path, b.data_class, r.uri FROM blobs b JOIN replicas r ON r.sha256 = b.sha256 AND r.location = 'cache' "
            f"AND r.state = 'present' WHERE b.data_class IN ({marks}) AND NOT EXISTS (SELECT 1 FROM replicas n WHERE n.sha256 = b.sha256 "
            f"AND n.location = 'nas' AND n.state = 'present')", tuple(classes))
        return sum(self._to_nas(r["sha256"], Path(r["uri"]), r["logical_path"], r["data_class"]) for r in rows if Path(r["uri"]).exists())

    # ── dispatch ─────────────────────────────────────────────────────────
    def publish(self, job: sqlite3.Row, result: RunResult, work_dir: Path) -> dict[str, Any]:
        plan = json.loads(job["plan_json"])
        handler = {"CALIB_MASTER": self._calib_master, "PROJECT_REFERENCE": self._reference,
                   "NIGHT_STACK": self._night_stack, "MERGE": self._merge}[job["kind"]]
        return handler(job, plan, result, work_dir)

    # ── calibration masters (§8) ─────────────────────────────────────────
    def _calib_master(self, job, plan, result: RunResult, work_dir: Path) -> dict:
        out = result.output("master")
        if out is None or not Path(out.path).exists():
            raise VerifyError("the runner reported no master")
        m = plan["master"]
        kind = m["kind"]

        def logical(sha: str) -> str:
            parts = [kind.lower(), m.get("filter"), f"{_fmt(m.get('exposure'))}s" if kind != "BIAS" else None,
                     f"g{m.get('gain')}", f"bin{m.get('binning')}", f"{_fmt(m.get('sensor_temp'))}C" if kind == "DARK" else None,
                     f"rot{_fmt(m.get('rotator_pos'))}" if kind == "FLAT" and m.get("rotator_pos") is not None else None]
            name = "_".join(str(p) for p in parts if p)
            return f"calibration/masters/{kind.lower()}/{m['rig']}/{m['night']}/master_{name}_{_sha8(sha)}{Path(out.path).suffix.lower()}"

        sha, _, _ = self.store(out.path, logical, "calibration_master", rig=m["rig"])
        with self.catalog.transaction() as tx:
            tx.execute(
                "INSERT OR IGNORE INTO calibration_masters(kind, sha256, source_frames_json, camera, telescope, filter, focal_length, exposure, "
                "gain, offset, sensor_temp, binning, readout_mode, width, height, rotator_pos, rotator_units, rig, night, n_frames, quality_json, "
                "taken_at, job_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, sha, json.dumps(plan["inputs"]), m["camera"], m.get("telescope"), m.get("filter"), m.get("focal_length"),
                 m.get("exposure"), m.get("gain"), m.get("offset"), m.get("sensor_temp"), m.get("binning"), m.get("readout_mode"),
                 m.get("width"), m.get("height"), m.get("rotator_pos"), m.get("rotator_units"), m["rig"], m["night"],
                 int(result.metrics.get("frames") or m.get("n_frames") or 0), json.dumps(result.metrics), m.get("taken_at"), job["id"]))
            master = tx.execute("SELECT * FROM calibration_masters WHERE sha256 = ?", (sha,)).fetchone()
            # A master rebuilt for the same night and key (more subs arrived) supersedes the older one.
            tx.execute("UPDATE calibration_masters SET superseded_by = ? WHERE id != ? AND superseded_by IS NULL AND kind = ? AND rig = ? "
                       "AND night = ? AND coalesce(filter, '') = coalesce(?, '') AND coalesce(exposure, -1) = coalesce(?, -1) "
                       "AND coalesce(gain, -1) = coalesce(?, -1) AND coalesce(offset, -1) = coalesce(?, -1) AND binning IS ? "
                       "AND coalesce(rotator_pos, -1) = coalesce(?, -1) AND imported = 0",
                       (master["id"], master["id"], kind, m["rig"], m["night"], m.get("filter"), m.get("exposure"), m.get("gain"),
                        m.get("offset"), m.get("binning"), m.get("rotator_pos")))
            marks = ",".join("?" * len(plan["inputs"]))
            tx.execute(f"UPDATE frames SET status = 'processed' WHERE sha256 IN ({marks}) AND status = 'valid'", tuple(plan["inputs"]))
            if self.config.hub.enabled:
                enqueue_calibration_master(tx, self.config, master["id"])
                for s in plan["inputs"]:
                    enqueue_frame_patch(tx, s, {"status": "processed"})
            self.auto_resolve(tx, dict(master))
        return {"master": sha, "calibration_master_id": master["id"]}

    def auto_resolve(self, tx: sqlite3.Connection, master: dict) -> list[int]:
        """A new master that satisfies an open issue's requirement queues a
        rerun of that issue's night (§10.4 auto-resolution)."""
        if not self.config.issues.auto_rerun_on_resolution:
            return []
        rules = self.config.calibration_matching
        queued = []
        marks = ",".join("?" * len(CALIB_ISSUE_KINDS))
        for issue in tx.execute(f"SELECT id, requirement_json FROM issues WHERE status = 'open' AND kind IN ({marks}) "
                                "AND requirement_json IS NOT NULL", CALIB_ISSUE_KINDS).fetchall():
            req = json.loads(issue["requirement_json"])
            need = req.get("need") or {}
            if req.get("kind") != master["kind"] or need.get("rig", master["rig"]) != master["rig"]:
                continue
            if master["kind"] == "DARK":
                cam = self.config.camera(need.get("camera"))
                hit = matching.match_dark(need, [master], rules, cooled=cam.cooled if cam else True)
            elif master["kind"] == "BIAS":
                hit = matching.match_bias(need, [master], rules)
            elif master["kind"] == "DARKFLAT":
                hit = matching.match_darkflat(need, [master], rules)
            else:
                rig = self.config.rigs.get(master["rig"])
                if rig is None:
                    continue
                events = [dict(r) for r in tx.execute("SELECT * FROM equipment_events").fetchall()]
                hit = matching.match_flat(need, [master], rules, rig.rotator, focal_tolerance=rig.focal_length_tolerance_mm, events=events)
            if isinstance(hit, matching.Match):
                tx.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES ('rerun', ?, 'auto:calibration_master', ?)",
                           (json.dumps({"issue_id": issue["id"]}), now_iso()))
                queued.append(issue["id"])
        return queued

    # ── the project reference (§9.2) ─────────────────────────────────────
    def _reference(self, job, plan, result: RunResult, work_dir: Path) -> dict:
        out = result.output("reference")
        if out is None or not Path(out.path).exists():
            raise VerifyError("the runner reported no reference frame")
        version = plan["version"]
        suffix = Path(out.path).suffix.lower()
        sha, _, _ = self.store(out.path, lambda s: f"{plan['project_path']}/reference/reference_v{version}_{_sha8(s)}{suffix}",
                               "project_reference", rig=plan["rig"])
        metrics = result.metrics
        with self.catalog.transaction() as tx:
            tx.execute("INSERT OR IGNORE INTO reference_frames(project_id, version, sha256, night, filter, source_sha256, metrics_json, job_id, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (plan["project_id"], version, sha, plan["night"], metrics.get("filter") or (plan.get("filters") or [None])[0],
                        out.source_sha256, json.dumps(metrics), job["id"], now_iso()))
            ref = tx.execute("SELECT id FROM reference_frames WHERE project_id = ? AND version = ? AND sha256 = ?",
                             (plan["project_id"], version, sha)).fetchone()
            tx.execute("UPDATE projects SET reference_sha256 = ?, reference_night = ?, pixel_scale_arcsec = coalesce(?, pixel_scale_arcsec) "
                       "WHERE id = ? AND reference_version = ? AND reference_sha256 IS NULL",
                       (sha, plan["night"], metrics.get("pixel_scale_arcsec"), plan["project_id"], version))
        products.render_preview(self.config, "project_reference", ref["id"], out.path)
        with self.catalog.transaction() as tx:
            products.enqueue(tx, self.config, "project_reference", ref["id"])
        return {"reference": sha, "reference_id": ref["id"]}

    # ── night stacks (§6.6) ──────────────────────────────────────────────
    def _night_stack(self, job, plan, result: RunResult, work_dir: Path) -> dict:
        project = self.catalog.one("SELECT * FROM projects WHERE id = ?", (plan["project_id"],))
        opts = settings(project, self.config)
        problems = verify.night_stack(result, plan, opts, self.config, read_image=self.read_images)
        if problems:
            raise VerifyError("; ".join(problems))
        final = plan["stack_kind"] == "final"
        master = result.output("master")
        suffix = Path(master.path).suffix.lower()
        base = f"{plan['project_path']}/nights/{plan['night']}/{plan['filter']}"
        prefix = "night_master" if final else "provisional_noflat"
        sha, _, size = self.store(master.path, lambda s: f"{base}/{prefix}_{_sha8(s)}{suffix}",
                                  "night_master" if final else "provisional_master", rig=plan["rig"], to_nas=final)
        all_lights = sorted({s for g in plan["groups"] for s in g["lights"]})
        per_frame = {f["sha256"]: f for f in result.frames if f.get("sha256")}
        used = sorted(s for s in all_lights if per_frame.get(s, {}).get("used", True))
        m = result.metrics
        exposure = {s: g["exposure"] for g in plan["groups"] for s in g["lights"]}
        metrics = {**m, "frames": len(used), "rejected": len(all_lights) - len(used),
                   "total_exposure_s": float(sum(exposure.get(s) or 0 for s in used)), "frame_sha256s": used}

        calibrated: list[str] = []
        if final and plan.get("keep_calibrated_frames", True):
            for out in result.outputs_of("calibrated_frame"):
                if not Path(out.path).exists():
                    continue
                stem, ext = Path(out.path).stem, Path(out.path).suffix.lower()
                c_sha, _, _ = self.store(out.path, lambda s, stem=stem, ext=ext: f"{base}/calibrated/{stem}_{_sha8(s)}{ext}",
                                         "calibrated_frame", rig=plan["rig"])
                calibrated.append(c_sha)

        sidecar = {"schema": 1, "project_id": plan["project_id"], "night": plan["night"], "filter": plan["filter"], "rig": plan["rig"],
                   "kind": plan["stack_kind"], "master_sha256": sha, "reference": plan.get("reference"),
                   "reference_version": plan["reference_version"], "groups": plan["groups"], "inputs": all_lights, "used": used,
                   "frames": result.frames, "metrics": metrics, "calibrated_frames": calibrated, "software": result.software,
                   "job_id": job["id"], "plan_hash": job["plan_hash"]}
        sidecar_path = work_dir / f"{prefix}_{_sha8(sha)}.json"
        sidecar_path.write_text(json.dumps(sidecar, indent=1, sort_keys=True, default=str), encoding="utf-8")
        side_sha, _, _ = self.store(sidecar_path, lambda s: f"{base}/{prefix}_{_sha8(sha)}.json", "metadata", rig=plan["rig"], to_nas=final)

        nas_uri = (blobs.replicas(self.catalog.conn, sha).get("nas") or {"uri": None})["uri"]
        with self.catalog.transaction() as tx:
            tx.execute(
                "INSERT OR IGNORE INTO night_masters(project_id, night, filter, sha256, input_frames_json, kind, reference_version, n_frames, "
                "n_rejected, total_exposure_s, calib_json, flat_verified, metrics_json, merge_status, job_id, nas_path, size_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (plan["project_id"], plan["night"], plan["filter"], sha, json.dumps(used), plan["stack_kind"], plan["reference_version"],
                 len(used), len(all_lights) - len(used), metrics["total_exposure_s"],
                 json.dumps({"groups": [{k: g.get(k) for k in ("dark", "flat", "flat_evidence", "rotator_pos", "exposure")} for g in plan["groups"]],
                             "calibrated": calibrated, "sidecar": side_sha, "drizzle_scale": plan.get("drizzle_scale", 1)}),
                 int(final), json.dumps(metrics), "pending" if final else "provisional", job["id"], nas_uri, size))
            row = tx.execute("SELECT * FROM night_masters WHERE project_id = ? AND night = ? AND filter = ? AND kind = ? AND reference_version = ? "
                             "AND sha256 = ?", (plan["project_id"], plan["night"], plan["filter"], plan["stack_kind"],
                                                plan["reference_version"], sha)).fetchone()
            # The newest master of a night supersedes older ones; a final one also the provisional.
            kinds = ("final", "provisional_noflat") if final else ("provisional_noflat",)
            tx.execute(f"UPDATE night_masters SET superseded_by = ? WHERE project_id = ? AND night = ? AND filter = ? AND id != ? "
                       f"AND superseded_by IS NULL AND kind IN ({','.join('?' * len(kinds))})",
                       (row["id"], plan["project_id"], plan["night"], plan["filter"], row["id"], *kinds))
            self._frame_results(tx, all_lights, per_frame, final)
        product_kind = "night_master" if final else "provisional_noflat"
        preview_source = (result.output("preview") or result.output("viewing") or master).path
        products.render_preview(self.config, product_kind, row["id"], preview_source)
        with self.catalog.transaction() as tx:
            products.enqueue(tx, self.config, product_kind, row["id"])
        self._publish_viewing_copy(project, plan, result, row, final)
        merge = None
        if final:
            merge = self.plan_merge(self.catalog, self.config, plan["project_id"], plan["filter"])
        return {"master": sha, "night_master_id": row["id"], "sidecar": side_sha, "calibrated": calibrated,
                "merge_job": merge.get("plan_hash") if isinstance(merge, dict) else None}

    def _frame_results(self, tx: sqlite3.Connection, lights: list[str], per_frame: dict, final: bool) -> None:
        """Used lights of a final stack become processed, rejected ones rejected;
        a provisional stack leaves them held (their flats are missing)."""
        for sha in lights:
            info = per_frame.get(sha, {})
            quality = {k: info[k] for k in ("fwhm", "eccentricity", "stars", "psf_signal_weight", "weight") if info.get(k) is not None}
            if final:
                status = "processed" if info.get("used", True) else "rejected"
                reason = None if status == "processed" else (info.get("reason") or "rejected by the night stack")
                tx.execute("UPDATE frames SET status = ?, status_reason = ?, quality_json = ? WHERE sha256 = ?",
                           (status, reason, json.dumps(quality) if quality else None, sha))
                change: dict[str, Any] = {"status": status, "status_reason": reason}
            else:
                tx.execute("UPDATE frames SET quality_json = ? WHERE sha256 = ?", (json.dumps(quality) if quality else None, sha))
                change = {}
            if quality:
                change["quality"] = quality
            if change and self.config.hub.enabled:
                enqueue_frame_patch(tx, sha, change)

    def _publish_viewing_copy(self, project, plan, result: RunResult, row, final: bool) -> Path | None:
        """A convenience copy under paths.published (§6.6 step 3); not a replica."""
        if not self.config.paths.published:
            return None
        source = Path((result.output("viewing") or result.output("master")).path)
        groups = plan["groups"]
        exposure = groups[0]["exposure"] if groups else 0
        rig = self.config.rigs[plan["rig"]]
        name = (f"{project_label(project)}_{rig.telescope}_{rig.camera}_{plan['filter']}_{plan['night']}_{row['n_frames']}x{_fmt(exposure)}s"
                + ("" if final else "_NOFLAT-PROVISIONAL") + source.suffix.lower())
        dest = Path(self.config.paths.published) / project_label(project) / plan["night"] / name
        return _copy_published(source, dest)

    # ── multi-night masters (§9.5) ───────────────────────────────────────
    def _merge(self, job, plan, result: RunResult, work_dir: Path) -> dict:
        out = result.output("master")
        if out is None or not Path(out.path).exists():
            raise VerifyError("the runner reported no multi-night master")
        existing = self.catalog.one("SELECT * FROM multi_night_masters WHERE plan_hash = ?", (job["plan_hash"],))
        version = existing["version"] if existing else (self.catalog.one(
            "SELECT coalesce(max(version), 0) + 1 AS v FROM multi_night_masters WHERE project_id = ? AND filter = ?",
            (plan["project_id"], plan["filter"]))["v"])
        base = f"{plan['project_path']}/multinight/{plan['filter']}"
        suffix = Path(out.path).suffix.lower()
        sha, _, size = self.store(out.path, lambda s: f"{base}/v{version:03d}_{_sha8(s)}{suffix}", "multi_night_master", rig=plan["rig"])
        extra = {}
        for role in ("coverage", "rejection_low", "rejection_high"):
            o = result.output(role)
            if o and Path(o.path).exists():
                ext = Path(o.path).suffix.lower()
                extra[role], _, _ = self.store(o.path, lambda s, role=role, ext=ext: f"{base}/v{version:03d}_{role}_{_sha8(s)}{ext}",
                                               "multi_night_master", rig=plan["rig"])
        weights = plan.get("weights") or {}
        total_w = sum(weights.values()) or 1.0
        inputs = [{"night": n["night"], "sha256": n["sha256"], "night_master_id": n["id"], "weight": weights.get(n["sha256"]),
                   "percent": 100.0 * (weights.get(n["sha256"]) or 0) / total_w, "frames": n.get("frames"), "exposure_s": n.get("exposure_s")}
                  for n in plan["nights"]]
        frames = sorted({s for n in plan["nights"] for s in n.get("frame_sha256s", [])})
        sidecar = {"schema": 1, "project_id": plan["project_id"], "filter": plan["filter"], "version": version, "sha256": sha,
                   "mode": plan["mode"], "weighting": plan["weighting"], "normalization": plan["normalization"], "inputs": inputs,
                   "excluded": plan.get("excluded", []), "outputs": extra, "metrics": result.metrics, "plan_hash": job["plan_hash"],
                   "reference": plan.get("reference"), "software": result.software}
        sidecar_path = work_dir / f"v{version:03d}.json"
        sidecar_path.write_text(json.dumps(sidecar, indent=1, sort_keys=True, default=str), encoding="utf-8")
        self.store(sidecar_path, lambda s: f"{base}/v{version:03d}_{_sha8(sha)}.json", "metadata", rig=plan["rig"])
        nas_uri = (blobs.replicas(self.catalog.conn, sha).get("nas") or {"uri": None})["uri"]
        with self.catalog.transaction() as tx:
            tx.execute("INSERT OR IGNORE INTO multi_night_masters(project_id, filter, version, sha256, inputs_json, excluded_json, total_exposure_s, "
                       "n_nights, plan_hash, created_at, nas_path, size_bytes, input_frames_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (plan["project_id"], plan["filter"], version, sha, json.dumps(inputs), json.dumps(plan.get("excluded", [])),
                        float(sum(n.get("exposure_s") or 0 for n in plan["nights"])), len(plan["nights"]), job["plan_hash"], now_iso(),
                        nas_uri, size, json.dumps(frames)))
            row = tx.execute("SELECT * FROM multi_night_masters WHERE plan_hash = ?", (job["plan_hash"],)).fetchone()
            tx.execute(f"UPDATE night_masters SET merge_status = 'merged' WHERE id IN ({','.join('?' * len(inputs))})",
                       tuple(i["night_master_id"] for i in inputs))
        products.render_preview(self.config, "multi_night_master", row["id"], (result.output("preview") or out).path)
        with self.catalog.transaction() as tx:
            products.enqueue(tx, self.config, "multi_night_master", row["id"])
        self._publish_multinight(plan, out, row)
        return {"master": sha, "multi_night_master_id": row["id"], "version": version, **extra}

    def _publish_multinight(self, plan: dict, out, row) -> None:
        if not self.config.paths.published:
            return
        project = self.catalog.one("SELECT * FROM projects WHERE id = ?", (plan["project_id"],))
        folder = Path(self.config.paths.published) / project_label(project) / "multinight"
        _copy_published(Path(out.path), folder / f"{project_label(project)}_{plan['filter']}_v{row['version']:03d}{Path(out.path).suffix.lower()}")
        keep = self.config.multi_night.keep_versions
        copies = sorted(folder.glob(f"{project_label(project)}_{plan['filter']}_v*"))
        for old in copies[:-keep] if keep else []:
            old.unlink(missing_ok=True)   # viewing copies only; archived versions stay

    # ── `altair publish --refresh` ───────────────────────────────────────
    def refresh(self) -> int:
        """Re-create the viewing copies from the canonical blobs (§6.6 step 3)."""
        if not self.config.paths.published:
            return 0
        count = 0
        for row in self.catalog.query("SELECT n.*, j.plan_json FROM night_masters n JOIN jobs j ON j.id = n.job_id WHERE n.superseded_by IS NULL"):
            path = _readable(self.catalog, row["sha256"])
            if path is None:
                continue
            plan = json.loads(row["plan_json"])
            project = self.catalog.one("SELECT * FROM projects WHERE id = ?", (row["project_id"],))
            fake = RunResult("ok", outputs=[Output("master", str(path))])
            count += self._publish_viewing_copy(project, plan, fake, row, row["kind"] == "final") is not None
        return count


def _readable(catalog: Catalog, sha: str) -> Path | None:
    for location in ("cache", "nas"):
        row = blobs.replicas(catalog.conn, sha).get(location)
        if blobs.verified(row) and Path(row["uri"]).exists():
            return Path(row["uri"])
    return None


def _copy_published(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    shutil.copyfile(source, partial)
    os.replace(partial, dest)
    return dest
