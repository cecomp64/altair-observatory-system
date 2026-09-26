"""Data products reported to the Hub (SPEC §6.6 step 6, §17.3).

A product's metadata is rebuilt from the catalog each time it is enqueued,
so the same call reports it when it is published (``archive_uri`` null) and
again once the replicator has uploaded it (``archive_uri`` set). The outbox
sends only the newest payload per natural key.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from altair.config import AltairConfig
from altair.hub.reporters import enqueue_data_product

log = logging.getLogger("altair.products")


def preview_paths(config: AltairConfig, kind: str, altair_id: int) -> tuple[Path, Path]:
    base = config.cache_dir / "previews"
    return base / f"{kind}_{altair_id}.jpg", base / f"{kind}_{altair_id}_thumb.jpg"


def render_preview(config: AltairConfig, kind: str, altair_id: int, source: str | Path) -> tuple[str | None, str | None]:
    if not config.hub.previews.enabled:
        return None, None
    from altair.hub.previews import render

    preview, thumb = preview_paths(config, kind, altair_id)
    p = config.hub.previews
    try:
        render(source, preview, thumb, long_edge=p.long_edge_px, thumb=p.thumb_px, quality=p.jpeg_quality)
    except Exception as exc:  # noqa: BLE001 - a missing preview never blocks publishing
        log.warning("no preview for %s %s: %s", kind, altair_id, exc)
        return None, None
    return str(preview), str(thumb)


def _archive(tx: sqlite3.Connection, sha: str) -> tuple[str | None, str | None]:
    reps = {r["location"]: r for r in tx.execute("SELECT * FROM replicas WHERE sha256 = ?", (sha,))}
    s3 = reps.get("s3")
    nas = reps.get("nas")
    archive = s3["uri"] if s3 and s3["state"] in ("present", "archived_cold", "restored") else None
    return archive, (nas["uri"] if nas and nas["state"] == "present" else None)


def _size(tx: sqlite3.Connection, sha: str) -> int:
    row = tx.execute("SELECT size_bytes FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
    return row["size_bytes"] if row else 0


def _metrics(raw: dict[str, Any]) -> dict[str, Any]:
    keep = ("frames", "rejected", "total_exposure_s", "fwhm", "eccentricity", "snr", "night_weights", "frame_sha256s")
    return {k: raw[k] for k in keep if raw.get(k) is not None}


def enqueue(tx: sqlite3.Connection, config: AltairConfig, kind: str, altair_id: int) -> bool:
    """kind: night_master / provisional_noflat / multi_night_master / project_reference / masters_bundle
    (``altair_id`` is then the project id)."""
    if not config.hub.enabled:
        return False
    extra: dict[str, Any] = {}
    if kind in ("night_master", "provisional_noflat"):
        row = tx.execute("SELECT n.*, p.hub_target_id FROM night_masters n JOIN projects p ON p.id = n.project_id WHERE n.id = ?",
                         (altair_id,)).fetchone()
        if row is None or row["hub_target_id"] is None:
            return False
        metrics = json.loads(row["metrics_json"] or "{}")
        metrics.setdefault("frame_sha256s", json.loads(row["input_frames_json"] or "[]"))
        prior = tx.execute("SELECT id FROM night_masters WHERE superseded_by = ? AND kind = ? ORDER BY id DESC LIMIT 1",
                           (altair_id, row["kind"])).fetchone()
        meta = {"target_id": row["hub_target_id"], "night": row["night"], "version": None, "filter": row["filter"],
                "supersedes_altair_id": prior["id"] if prior else None}
        bundle_sha = json.loads(row["calib_json"] or "{}").get("calibrated_bundle")
        if bundle_sha:   # the night's calibrated subs, one zip (api_revision 4)
            frames = tx.execute("SELECT count(*) AS n FROM bundle_members WHERE bundle_sha256 = ?", (bundle_sha,)).fetchone()["n"]
            extra["calibrated_bundle"] = {"sha256": bundle_sha, "size_bytes": _size(tx, bundle_sha), "archive_uri": _archive(tx, bundle_sha)[0],
                                          "frames": frames}
    elif kind == "masters_bundle":
        row = tx.execute("SELECT b.*, p.hub_target_id FROM masters_bundles b JOIN projects p ON p.id = b.project_id WHERE b.project_id = ?",
                         (altair_id,)).fetchone()
        if row is None or row["hub_target_id"] is None:
            return False
        contents = json.loads(row["contents_json"])
        metrics = {"total_exposure_s": sum(c.get("total_exposure_s") or 0 for c in contents)}
        meta = {"target_id": row["hub_target_id"], "night": None, "version": row["build"], "filter": "all", "supersedes_altair_id": None}
        extra["contents"] = [{"filter": c["filter"], "version": c["version"], "name": c["name"]} for c in contents]
    elif kind == "multi_night_master":
        row = tx.execute("SELECT m.*, p.hub_target_id FROM multi_night_masters m JOIN projects p ON p.id = m.project_id WHERE m.id = ?",
                         (altair_id,)).fetchone()
        if row is None or row["hub_target_id"] is None:
            return False
        inputs = json.loads(row["inputs_json"])
        metrics = {"frames": sum(i.get("frames") or 0 for i in inputs), "total_exposure_s": row["total_exposure_s"] or 0,
                   "night_weights": {i["night"]: round(float(i.get("percent") or 0), 3) for i in inputs},
                   "frame_sha256s": json.loads(row["input_frames_json"] or "[]")}
        prior = tx.execute("SELECT id FROM multi_night_masters WHERE project_id = ? AND filter = ? AND version < ? ORDER BY version DESC LIMIT 1",
                           (row["project_id"], row["filter"], row["version"])).fetchone()
        meta = {"target_id": row["hub_target_id"], "night": None, "version": row["version"], "filter": row["filter"],
                "supersedes_altair_id": prior["id"] if prior else None}
    elif kind == "project_reference":
        row = tx.execute("SELECT * FROM reference_frames WHERE id = ?", (altair_id,)).fetchone()
        project = tx.execute("SELECT hub_target_id FROM projects WHERE id = ?", (row["project_id"],)).fetchone() if row else None
        if row is None or project is None or project["hub_target_id"] is None:
            return False
        metrics = json.loads(row["metrics_json"] or "{}")
        meta = {"target_id": project["hub_target_id"], "night": row["night"], "version": row["version"], "filter": row["filter"] or "",
                "supersedes_altair_id": None}
    else:
        raise ValueError(kind)
    archive, nas = _archive(tx, row["sha256"])
    meta.update({"sha256": row["sha256"], "size_bytes": _size(tx, row["sha256"]), "archive_uri": archive, "nas_path": nas,
                 "metrics": _metrics(metrics), **extra})
    from altair.reports import report_path

    preview, thumb = preview_paths(config, kind, altair_id)
    report = report_path(config, kind, altair_id)
    enqueue_data_product(tx, kind, altair_id, meta, str(preview) if preview.exists() else None, str(thumb) if thumb.exists() else None,
                         report=str(report) if report.exists() else None)
    return True


def reenqueue_for_blob(tx: sqlite3.Connection, config: AltairConfig, sha: str) -> int:
    """After an upload: report the products stored as this blob again, now with their archive URI."""
    count = 0
    for row in tx.execute("SELECT id, kind FROM night_masters WHERE sha256 = ?", (sha,)).fetchall():
        count += enqueue(tx, config, "night_master" if row["kind"] == "final" else "provisional_noflat", row["id"])
    for row in tx.execute("SELECT id FROM multi_night_masters WHERE sha256 = ?", (sha,)).fetchall():
        count += enqueue(tx, config, "multi_night_master", row["id"])
    for row in tx.execute("SELECT id FROM reference_frames WHERE sha256 = ?", (sha,)).fetchall():
        count += enqueue(tx, config, "project_reference", row["id"])
    for row in tx.execute("SELECT project_id FROM masters_bundles WHERE sha256 = ?", (sha,)).fetchall():
        count += enqueue(tx, config, "masters_bundle", row["project_id"])
    for row in tx.execute("SELECT id, kind FROM night_masters WHERE json_extract(calib_json, '$.calibrated_bundle') = ?", (sha,)).fetchall():
        count += enqueue(tx, config, "night_master" if row["kind"] == "final" else "provisional_noflat", row["id"])
    return count
