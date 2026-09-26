"""Rebuild the catalog from the archive (SPEC §7.9), without reading any image.

The archive describes itself:
- collection manifests (``raw/<rig>/_manifests/``) list every frame with its
  hash and headers;
- sidecars sit next to every calibration master, project reference, night
  master and multi-night master.

This module walks the NAS and/or S3, and from those files restores:
blobs and replicas, frames, collections, calibration masters, projects and
their references, night masters (with their calibrated subs) and multi-night
history. Issues are not restored: re-planning derives them again.

Replicas found this way are recorded with ``verify_method = 'rebuild'`` (the
file is there with the right size). The scrubber re-verifies them over time.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.storage import blobs
from altair.storage.locations import COLD_CLASSES, FsLocation, S3Location

log = logging.getLogger("altair.rebuild")


@dataclass
class RebuildReport:
    frames: int = 0
    frames_without_copy: int = 0
    collections: int = 0
    calibration_masters: int = 0
    projects: int = 0
    references: int = 0
    night_masters: int = 0
    multi_night_masters: int = 0
    blobs: int = 0
    skipped: list[str] = field(default_factory=list)


class FsSource:
    def __init__(self, location: FsLocation):
        self.location = location
        self.name = location.name

    def json_files(self, prefix: str) -> Iterator[tuple[str, bytes]]:
        base = self.location.path(prefix)
        if not base.exists():
            return
        for path in sorted(base.rglob("*.json")):
            try:
                yield path.relative_to(self.location.root).as_posix(), path.read_bytes()
            except OSError as exc:
                log.warning("unreadable %s: %s", path, exc)

    def find(self, logical: str, size: int | None) -> dict | None:
        path = self.location.path(logical)
        if not path.exists() or (size is not None and path.stat().st_size != size):
            return None
        return {"uri": str(path), "storage_class": None}

    def files(self, prefix: str) -> Iterator[tuple[str, int]]:
        base = self.location.path(prefix)
        for path in sorted(base.rglob("*")) if base.exists() else []:
            if path.is_file() and not path.name.endswith(".partial"):
                yield path.relative_to(self.location.root).as_posix(), path.stat().st_size


class S3Source:
    def __init__(self, location: S3Location):
        self.location = location
        self.name = "s3"
        self._objects = {self.location.logical_path(o["Key"]): o for o in location.list("")}

    def json_files(self, prefix: str) -> Iterator[tuple[str, bytes]]:
        for logical in sorted(k for k in self._objects if k.startswith(prefix) and k.endswith(".json")):
            yield logical, self.location.client.get_object(Bucket=self.location.bucket, Key=self.location.key(logical))["Body"].read()

    def find(self, logical: str, size: int | None) -> dict | None:
        obj = self._objects.get(logical)
        if obj is None or (size is not None and obj["Size"] != size):
            return None
        return {"uri": self.location.uri(logical), "storage_class": obj.get("StorageClass", "STANDARD")}

    def files(self, prefix: str) -> Iterator[tuple[str, int]]:
        for logical, obj in sorted(self._objects.items()):
            if logical.startswith(prefix):
                yield logical, obj["Size"]


class Rebuilder:
    def __init__(self, catalog: Catalog, config: AltairConfig, sources: list):
        if not sources:
            raise ValueError("no source to rebuild from")
        self.catalog = catalog
        self.config = config
        self.sources = sources
        self.report = RebuildReport()
        self.project_ids: dict[str, int] = {}     # project path → new id

    # ── helpers ──────────────────────────────────────────────────────────
    def _json(self, prefix: str, rig_of=lambda body: None) -> Iterator[tuple[str, dict]]:
        """Each JSON file once, from the first source that has it; it is
        registered as a metadata blob (sidecars and manifests are small, and
        are the only files a rebuild reads)."""
        seen: set[str] = set()
        for source in self.sources:
            for logical, raw in source.json_files(prefix):
                if logical in seen:
                    continue
                seen.add(logical)
                try:
                    body = json.loads(raw)
                except ValueError as exc:
                    log.warning("unreadable %s: %s", logical, exc)
                    continue
                if isinstance(body, dict):
                    self._blob(hashlib.sha256(raw).hexdigest(), logical, len(raw), "metadata", rig_of(body))
                    yield logical, body

    def _replicas(self, logical: str, size: int | None) -> list[tuple[str, dict]]:
        return [(s.name, hit) for s in self.sources if (hit := s.find(logical, size))]

    def _blob(self, sha: str, logical: str, size: int, data_class: str, rig: str | None = None) -> bool:
        reps = self._replicas(logical, size)
        with self.catalog.transaction() as tx:
            self.report.blobs += blobs.add_blob(tx, sha, size, data_class, logical, rig)
            for location, hit in reps:
                cold = (hit["storage_class"] or "") in COLD_CLASSES
                blobs.set_replica(tx, sha, location, hit["uri"], kind="s3" if location == "s3" else "fs", method="rebuild",
                                  state="archived_cold" if cold else "present", storage_class=hit["storage_class"])
        return bool(reps)

    # ── the rebuild ──────────────────────────────────────────────────────
    def run(self) -> RebuildReport:
        self._nas_identity()
        self._manifests()
        self._calibration_masters()
        self._projects_and_products()
        self._catalog_backups()
        self._gates()
        self.catalog.execute("UPDATE replicas SET verify_method = 'rebuild' WHERE verify_method IS NULL OR verify_method = 'sha256_full'")
        return self.report

    def _nas_identity(self) -> None:
        for source in self.sources:
            if isinstance(source, FsSource):
                ident = source.location.root / self.config.storage.nas.health_check.identity_file
                if ident.exists():
                    self.catalog.set_state("nas_identity", json.loads(ident.read_text(encoding="utf-8")))

    def _manifests(self) -> None:
        from altair.ingest.ingest import ingest

        latest: dict[tuple[str, str], tuple[str, dict]] = {}
        for logical, manifest in self._json("raw/", lambda body: body.get("rig")):
            if "/_manifests/" not in logical or manifest.get("schema") != 1:
                continue
            key = (manifest["rig"], manifest["night"])
            if key not in latest or _manifest_version(logical) > _manifest_version(latest[key][0]):
                latest[key] = (logical, manifest)
        for (rig, night), (_, manifest) in sorted(latest.items()):
            if rig not in self.config.rigs:
                self.report.skipped.append(f"manifest for unknown rig {rig} ({night})")
                continue
            for f in manifest["files"]:
                reps = self._replicas(f["logical_path"], f["size"])
                if not reps:
                    self.report.frames_without_copy += 1
                    continue
                ingest(self.catalog, self.config, None, rig=rig, header=f["headers"], sha256=f["sha256"], size=f["size"],
                       logical_path=f["logical_path"], file_name=Path(f["logical_path"]).name,
                       replicas=[(loc, hit["uri"]) for loc, hit in reps], origin="rebuild")
                self.report.frames += 1
                for loc, hit in reps:
                    if hit["storage_class"]:
                        with self.catalog.transaction() as tx:
                            blobs.set_replica(tx, f["sha256"], loc, hit["uri"], kind="s3", method="rebuild", storage_class=hit["storage_class"],
                                              state="archived_cold" if hit["storage_class"] in COLD_CLASSES else "present")
            with self.catalog.transaction() as tx:
                tx.execute("INSERT OR REPLACE INTO collections(rig, night, state, closed_by, n_files, closed_at) VALUES (?, ?, 'closed', ?, ?, ?)",
                           (rig, night, manifest.get("closed_by") or "rebuild", len(manifest["files"]), now_iso()))
            self.report.collections += 1

    def _calibration_masters(self) -> None:
        for logical, side in self._json("calibration/masters/", lambda body: (body.get("master") or {}).get("rig")):
            if side.get("type") != "calibration_master":
                continue
            b, m = side["blob"], side["master"]
            self._blob(b["sha256"], b["logical_path"], b["size"], "calibration_master", m.get("rig"))
            with self.catalog.transaction() as tx:
                self.report.calibration_masters += tx.execute(
                    "INSERT OR IGNORE INTO calibration_masters(kind, sha256, source_frames_json, camera, telescope, filter, focal_length, exposure, "
                    "gain, offset, sensor_temp, binning, readout_mode, width, height, rotator_pos, rotator_units, rig, night, n_frames, "
                    "quality_json, taken_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (m["kind"], b["sha256"], json.dumps(side.get("source_frames") or []), m["camera"], m.get("telescope"), m.get("filter"),
                     m.get("focal_length"), m.get("exposure"), m.get("gain"), m.get("offset"), m.get("sensor_temp"), m.get("binning"),
                     m.get("readout_mode"), m.get("width"), m.get("height"), m.get("rotator_pos"), m.get("rotator_units"), m.get("rig"),
                     m.get("night"), m.get("n_frames"), json.dumps(side.get("metrics") or {}), m.get("taken_at"))).rowcount
        # The newest master of the same night and key supersedes the others, as when they were built.
        with self.catalog.transaction() as tx:
            tx.execute("UPDATE calibration_masters SET superseded_by = (SELECT max(n.id) FROM calibration_masters n WHERE n.kind = "
                       "calibration_masters.kind AND n.rig = calibration_masters.rig AND n.night = calibration_masters.night AND "
                       "coalesce(n.filter, '') = coalesce(calibration_masters.filter, '') AND coalesce(n.exposure, -1) = "
                       "coalesce(calibration_masters.exposure, -1) AND coalesce(n.rotator_pos, -1) = coalesce(calibration_masters.rotator_pos, -1) "
                       "AND n.id > calibration_masters.id)")

    def _project(self, block: dict) -> int:
        if block["path"] in self.project_ids:
            return self.project_ids[block["path"]]
        with self.catalog.transaction() as tx:
            row = tx.execute("SELECT id FROM projects WHERE path = ?", (block["path"],)).fetchone()
            if row is None:
                pid = tx.execute("INSERT INTO projects(target, telescope, camera, rig, hub_target_id, hub_project_id, drizzle_scale, "
                                 "multi_night_mode, reference_version, settings_json, path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (block["target"], block["telescope"], block["camera"], block["rig"], block["hub_target_id"],
                                  block.get("hub_project_id"), block.get("drizzle_scale") or 1, block.get("multi_night_mode") or "master_merge",
                                  block.get("reference_version") or 1, block.get("settings_json"), block["path"], block.get("created_at"))).lastrowid
                self.report.projects += 1
            else:
                pid = row["id"]
                tx.execute("UPDATE projects SET reference_version = max(reference_version, ?) WHERE id = ?", (block.get("reference_version") or 1, pid))
        self.project_ids[block["path"]] = pid
        return pid

    def _projects_and_products(self) -> None:
        sidecars = [(logical, side) for logical, side in self._json("projects/", lambda body: (body.get("project") or {}).get("rig"))
                    if side.get("type")]
        for logical, side in sorted(sidecars, key=lambda x: (x[1]["type"] != "project_reference", x[0])):
            pid = self._project(side["project"])
            rig = side["project"].get("rig")
            b = side["blob"]
            if side["type"] == "project_reference":
                self._blob(b["sha256"], b["logical_path"], b["size"], "project_reference", rig)
                with self.catalog.transaction() as tx:
                    self.report.references += tx.execute(
                        "INSERT OR IGNORE INTO reference_frames(project_id, version, sha256, night, filter, source_sha256, metrics_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (pid, side["version"], b["sha256"], side.get("night"), (side.get("metrics") or {}).get("filter"),
                                                            side.get("source_sha256"), json.dumps(side.get("metrics") or {}), now_iso())).rowcount
                    tx.execute("UPDATE projects SET reference_sha256 = ?, reference_night = ?, pixel_scale_arcsec = ? WHERE id = ? AND reference_version = ?",
                               (b["sha256"], side.get("night"), (side.get("metrics") or {}).get("pixel_scale_arcsec"), pid, side["version"]))
            elif side["type"] == "night_master":
                self._night_master(pid, side, rig)
            elif side["type"] == "multi_night_master":
                self._multi_night(pid, side, rig)

    def _night_master(self, pid: int, side: dict, rig: str | None) -> None:
        b = side["blob"]
        final = side["kind"] == "final"
        self._blob(b["sha256"], b["logical_path"], b["size"], b["class"], rig)
        for c in side.get("calibrated_blobs") or []:
            self._blob(c["sha256"], c["logical_path"], c["size"], "calibrated_frame", rig)
        m = side.get("metrics") or {}
        used = side.get("used") or []
        with self.catalog.transaction() as tx:
            self.report.night_masters += tx.execute(
                "INSERT OR IGNORE INTO night_masters(project_id, night, filter, sha256, input_frames_json, kind, reference_version, n_frames, "
                "n_rejected, total_exposure_s, calib_json, flat_verified, metrics_json, merge_status, job_id, size_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, side["night"], side["filter"], b["sha256"], json.dumps(used), side["kind"], side["reference_version"], m.get("frames"),
                 m.get("rejected"), m.get("total_exposure_s"),
                 json.dumps({"groups": side.get("groups"), "calibrated": side.get("calibrated_frames") or [], "drizzle_scale": side["project"].get("drizzle_scale")}),
                 int(final), json.dumps(m), "pending" if final else "provisional", side.get("job_id"), b["size"])).rowcount
            project = tx.execute("SELECT hub_target_id FROM projects WHERE id = ?", (pid,)).fetchone()
            for frame in side.get("frames") or []:
                status = ("processed" if frame.get("used", True) else "rejected") if final else None
                quality = {k: frame[k] for k in ("fwhm", "eccentricity", "stars", "psf_signal_weight", "weight") if frame.get(k) is not None}
                tx.execute("UPDATE frames SET status = coalesce(?, status), quality_json = ?, hub_target_id = coalesce(hub_target_id, ?) WHERE sha256 = ?",
                           (status, json.dumps(quality) if quality else None, project["hub_target_id"], frame["sha256"]))
            # Newest wins: a final supersedes the provisional of the same night,
            # a newer reference version or a later build the earlier one.
            rows = tx.execute("SELECT id FROM night_masters WHERE project_id = ? AND night = ? AND filter = ? "
                              "ORDER BY kind = 'final', reference_version, coalesce(job_id, 0), id", (pid, side["night"], side["filter"])).fetchall()
            for older in rows[:-1]:
                tx.execute("UPDATE night_masters SET superseded_by = ? WHERE id = ?", (rows[-1]["id"], older["id"]))
            tx.execute("UPDATE night_masters SET superseded_by = NULL WHERE id = ?", (rows[-1]["id"],))

    def _multi_night(self, pid: int, side: dict, rig: str | None) -> None:
        b = side["blob"]
        self._blob(b["sha256"], b["logical_path"], b["size"], "multi_night_master", rig)
        for extra in (side.get("extra_blobs") or {}).values():
            self._blob(extra["sha256"], extra["logical_path"], extra["size"], "multi_night_master", rig)
        with self.catalog.transaction() as tx:
            self.report.multi_night_masters += tx.execute(
                "INSERT OR IGNORE INTO multi_night_masters(project_id, filter, version, sha256, inputs_json, excluded_json, total_exposure_s, "
                "n_nights, plan_hash, created_at, size_bytes, input_frames_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, side["filter"], side["version"], b["sha256"], json.dumps(side.get("inputs") or []), json.dumps(side.get("excluded") or []),
                 side.get("total_exposure_s"), len(side.get("inputs") or []), side["plan_hash"], now_iso(), b["size"],
                 json.dumps(side.get("input_frames") or []))).rowcount

    def _catalog_backups(self) -> None:
        import re

        for source in self.sources:
            for logical, size in source.files("catalog/"):
                if re.fullmatch(r"catalog/altair-\d{8}T\d{6}Z\.db\.zst", logical):
                    # The hash isn't known without reading the file; catalog backups are found by listing, not by hash.
                    self.report.skipped.append(f"catalog backup {logical} (restore it with `altair storage restore-catalog`)")
                    break

    def _gates(self) -> None:
        """Merge statuses from the gates; the nights of each latest multi-night master count as merged."""
        from altair.projects.merge import _record, evaluate

        for row in self.catalog.query("SELECT DISTINCT project_id, filter FROM night_masters"):
            project = self.catalog.one("SELECT * FROM projects WHERE id = ?", (row["project_id"],))
            gates = evaluate(self.catalog, self.config, project, row["filter"])
            _record(self.catalog, self.config, project, row["filter"], gates)
            latest = self.catalog.one("SELECT inputs_json FROM multi_night_masters WHERE project_id = ? AND filter = ? ORDER BY version DESC LIMIT 1",
                                      (row["project_id"], row["filter"]))
            shas = [i["sha256"] for i in json.loads(latest["inputs_json"])] if latest else []
            if shas:
                self.catalog.execute(f"UPDATE night_masters SET merge_status = 'merged' WHERE project_id = ? AND merge_status = 'eligible' "
                                     f"AND sha256 IN ({','.join('?' * len(shas))})", (row["project_id"], *shas))


def _manifest_version(logical: str) -> int:
    """``<night>.json`` is version 1, ``<night>.vN.json`` version N."""
    import re

    m = re.search(r"\.v(\d+)\.json$", logical)
    return int(m[1]) if m else 1
