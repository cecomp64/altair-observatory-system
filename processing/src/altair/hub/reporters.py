"""Catalog rows -> Hub payloads (docs/SYSTEM_ARCHITECTURE.md §5.3). Each
builder returns a dict in the contract shape; ``enqueue_*`` helpers put it
in the outbox inside the caller's transaction."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from altair.config import AltairConfig
from altair.hub import outbox

SUMMARY_HEADERS = {"IMAGETYP", "OBJECT", "FILTER", "EXPTIME", "EXPOSURE", "DATE-OBS", "TELESCOP", "INSTRUME", "GAIN",
                   "OFFSET", "CCD-TEMP", "XBINNING", "YBINNING", "RA", "DEC", "FOCALLEN", "ROTATOR"}


def _hub_ids(config: AltairConfig, rig: str | None) -> tuple[str, str] | None:
    rig_cfg = config.rigs.get(rig or "")
    return (rig_cfg.hub.telescope, rig_cfg.hub.optical_train) if rig_cfg and rig_cfg.hub else None


def frame_payload(row: sqlite3.Row, config: AltairConfig, logical_path: str, storage: dict | None = None) -> dict[str, Any] | None:
    ids = _hub_ids(config, row["rig"])
    if not ids:
        return None  # rigs not mapped to the Hub stay local
    headers = json.loads(row["raw_headers_json"] or "{}")
    if config.hub.frame_headers == "summary":
        headers = {k: v for k, v in headers.items() if k in SUMMARY_HEADERS}
    payload = {
        "sha256": row["sha256"], "altair_frame_id": row["id"], "origin": row["origin"] or "collect",
        "telescope": ids[0], "optical_train": ids[1], "target_id": row["hub_target_id"],
        "assignment_source": row["assignment_source"] or ("unlinked" if row["hub_target_id"] is None else "header_token"),
        "image_type": row["image_type"], "night": row["night"], "date_obs": row["date_obs"],
        "object_header": row["target"], "filter": row["filter"], "exposure_s": row["exposure"], "gain": row["gain"],
        "offset": row["offset"], "binning": row["binning"], "readout_mode": row["readout_mode"],
        "sensor_temp_c": row["sensor_temp"], "rotator_pos": row["rotator_pos"], "rotator_units": row["rotator_units"],
        "ra_deg": row["ra_deg"], "dec_deg": row["dec_deg"], "rotation_deg": row["rotation_deg"],
        "width_px": row["width"], "height_px": row["height"], "file_name": row["file_name"],
        "logical_path": logical_path, "status": _hub_status(row["status"]), "status_reason": row["status_reason"],
        "storage": storage or {"nas": False, "s3": None}, "headers": _jsonable(headers),
    }
    if row["quality_json"]:
        payload["quality"] = json.loads(row["quality_json"])
    return {k: v for k, v in payload.items() if v is not None or k in ("target_id",)}


def _hub_status(status: str) -> str:
    return "collected" if status == "new" else status


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def enqueue_frame(tx: sqlite3.Connection, config: AltairConfig, frame_id: int) -> None:
    row = tx.execute("SELECT f.*, b.logical_path FROM frames f JOIN blobs b ON b.sha256 = f.sha256 WHERE f.id = ?", (frame_id,)).fetchone()
    replicas = {r["location"]: r for r in tx.execute("SELECT * FROM replicas WHERE sha256 = ?", (row["sha256"],))}
    s3 = replicas.get("s3")
    storage = {"nas": "nas" in replicas and replicas["nas"]["state"] == "present",
               "s3": s3["storage_class"] if s3 and s3["state"] in ("present", "archived_cold") else None}
    payload = frame_payload(row, config, row["logical_path"], storage)
    if payload:
        outbox.enqueue(tx, "frame", row["sha256"], payload)


def enqueue_frame_patch(tx: sqlite3.Connection, sha256: str, changes: dict[str, Any]) -> None:
    outbox.enqueue(tx, "frame_patch", sha256, {"sha256": sha256, **changes}, coalesce_merge=True)


def enqueue_night(tx: sqlite3.Connection, config: AltairConfig, rig: str, night: str) -> None:
    ids = _hub_ids(config, rig)
    row = tx.execute("SELECT * FROM collections WHERE rig = ? AND night = ?", (rig, night)).fetchone()
    if not ids or not row:
        return
    counts = tx.execute(
        "SELECT sum(image_type = 'light') AS lights, sum(image_type != 'light') AS cal, "
        "coalesce(sum(CASE WHEN image_type = 'light' THEN exposure END), 0) AS secs FROM frames WHERE rig = ? AND night = ?",
        (rig, night),
    ).fetchone()
    body = {"state": row["state"], "closed_by": row["closed_by"], "session_end_at": row["session_end_at"],
            "lights_count": counts["lights"] or 0, "calibration_count": counts["cal"] or 0,
            "light_seconds": counts["secs"] or 0, "manifest_sha256": row["manifest_sha256"]}
    outbox.enqueue(tx, "night", f"{ids[1]}:{night}", {"optical_train": ids[1], "night": night,
                                                       "body": {k: v for k, v in body.items() if v is not None}})


def enqueue_issue(tx: sqlite3.Connection, config: AltairConfig, issue_id: int) -> None:
    row = tx.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
    scope = json.loads(row["scope_json"] or "{}")
    ids = _hub_ids(config, scope.get("rig"))
    body = {
        "altair_id": row["id"], "kind": row["kind"], "severity": row["severity"], "status": row["status"],
        "message": row["message"], "requirement": json.loads(row["requirement_json"]) if row["requirement_json"] else None,
        "scope": scope, "optical_train": ids[1] if ids else None, "target_id": scope.get("hub_target_id"),
        "night": scope.get("night"), "filter": scope.get("filter"), "resolution": row["resolution"],
    }
    outbox.enqueue(tx, "issue", row["fingerprint"], {"fingerprint": row["fingerprint"], "body": body})


def enqueue_job(tx: sqlite3.Connection, job_id: int) -> None:
    row = tx.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    scope = json.loads(row["scope_json"] or "{}")
    status = "skipped" if row["status"] == "superseded" else row["status"]   # the Hub has no 'superseded'
    body = {"kind": row["kind"], "status": status, "target_id": scope.get("hub_target_id"), "night": scope.get("night"),
            "filter": scope.get("filter"), "started_at": row["started_at"], "finished_at": row["finished_at"], "error": row["error"]}
    outbox.enqueue(tx, "job", f"job:{job_id}", {"altair_id": job_id, "body": body})


def enqueue_calibration_master(tx: sqlite3.Connection, config: AltairConfig, master_id: int) -> None:
    row = tx.execute("SELECT * FROM calibration_masters WHERE id = ?", (master_id,)).fetchone()
    ids = _hub_ids(config, row["rig"])
    if not ids:
        return
    body = {"optical_train": ids[1], "kind": row["kind"].lower(), "filter": row["filter"], "exposure_s": row["exposure"],
            "gain": row["gain"], "offset": row["offset"], "binning": row["binning"], "sensor_temp_c": row["sensor_temp"],
            "rotator_pos": row["rotator_pos"], "night": row["night"], "n_frames": row["n_frames"] or 1,
            "sha256": row["sha256"], "superseded": row["superseded_by"] is not None}
    outbox.enqueue(tx, "calibration_master", f"calibration_master:{master_id}", {"altair_id": master_id, "body": body})


def enqueue_data_product(tx: sqlite3.Connection, kind: str, altair_id: int, metadata: dict[str, Any],
                         preview: str | None = None, thumbnail: str | None = None) -> None:
    """kind: night_master / multi_night_master / project_reference / provisional_noflat."""
    outbox.enqueue(tx, "data_product", f"{kind}:{altair_id}", {"kind": kind, "altair_id": altair_id, "metadata": metadata},
                   attachments={"preview": preview, "thumbnail": thumbnail})
