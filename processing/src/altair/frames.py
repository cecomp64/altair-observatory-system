"""Registering and re-linking frames: the code paths shared by `altair
index`, the collector and Hub commands (SPEC §6.2, §17.2)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.hub.reporters import enqueue_frame
from altair.issues import raise_issue, resolve_issue
from altair.storage import blobs


def unresolved_fingerprint(rig: str, night: str, object_header: str | None) -> str:
    return f"PROJECT_UNRESOLVED:{rig}:{night}:{object_header or ''}"


def register(catalog: Catalog, config: AltairConfig, *, sha256: str, size: int, logical_path: str, data_class: str,
             location: str, uri: str, fields: dict[str, Any], rig: str, origin: str, file_name: str, headers: dict[str, Any],
             resolution: tuple[int | None, str] | None, verified_at: str | None = None,
             extra_replicas: list[tuple[str, str]] = (), status: str | None = None, status_reason: str | None = None,
             on_created: Callable[[sqlite3.Connection, int], None] | None = None) -> tuple[int, bool]:
    """Blob + replicas + frame + outbox item, in one transaction. Returns
    (frame_id, created). A blob that is already known is only linked to the
    new replicas; it is never registered twice. ``status`` (from ingest
    validation) overrides the default; ``on_created`` runs in the same
    transaction for a new frame."""
    with catalog.transaction() as tx:
        existing = tx.execute("SELECT id FROM frames WHERE sha256 = ?", (sha256,)).fetchone()
        blobs.add_blob(tx, sha256, size, data_class, logical_path, rig)
        for loc, loc_uri in [(location, uri), *extra_replicas]:
            blobs.set_replica(tx, sha256, loc, loc_uri, verified_at=verified_at)
        if existing:
            return existing["id"], False

        target_id, source = resolution or (None, None)
        is_light = fields["image_type"] == "light"
        if status is None:
            status = "valid"
            if is_light and target_id is None and config.hub.enabled and config.hub.require_target_link:
                status = "held"
        frame_id = tx.execute(
            """INSERT INTO frames(sha256, image_type, night, date_obs, rig, telescope, camera, filter, target, focal_length, exposure,
                 gain, offset, sensor_temp, binning, readout_mode, width, height, bayer_pattern, rotator_pos, rotator_units,
                 ra_deg, dec_deg, rotation_deg, raw_headers_json, status, status_reason, origin, file_name, hub_target_id, assignment_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sha256, fields["image_type"], str(fields["night"]), fields["date_obs"].isoformat().replace("+00:00", "Z"), rig,
             fields["telescope"], fields["camera"], fields["filter"], fields["target"], fields["focal_length"], fields["exposure"],
             fields["gain"], fields["offset"], fields["sensor_temp"], fields["binning"], fields["readout_mode"],
             _int(fields["width"]), _int(fields["height"]), fields["bayer_pattern"], fields["rotator_pos"], fields["rotator_units"],
             fields["ra_deg"], fields["dec_deg"], fields["rotation_deg"], json.dumps(headers, default=str), status, status_reason, origin, file_name,
             target_id, source if is_light else None),
        ).lastrowid
        if status == "held":
            night = str(fields["night"])
            raise_issue(tx, config, kind="PROJECT_UNRESOLVED", severity="blocking",
                        fingerprint=unresolved_fingerprint(rig, night, fields["target"]),
                        message=f"Lights on {rig} for the night of {night} match no Hub target (OBJECT {fields['target']!r}). Assign them in the Hub.",
                        scope={"rig": rig, "night": night, "object": fields["target"], "filter": fields["filter"]},
                        requirement={"assign_target": True})
        if on_created:
            on_created(tx, frame_id)
        if config.hub.enabled:
            enqueue_frame(tx, config, frame_id)
        return frame_id, True


def assign(catalog: Catalog, config: AltairConfig, *, target_id: int, sha256s: list[str] | None = None, rig: str | None = None,
           night: str | None = None, object_header: str | None = None, source: str = "cli") -> int:
    """Manual assignment (Hub command or `altair frames assign`): never
    overridden by the resolution rules; releases held lights, resolves
    PROJECT_UNRESOLVED once a group is fully assigned, and re-plans both
    the old and the new project."""
    with catalog.transaction() as tx:
        if sha256s is not None:
            marks = ",".join("?" * len(sha256s))
            rows = tx.execute(f"SELECT * FROM frames WHERE sha256 IN ({marks})", sha256s).fetchall() if sha256s else []
        else:
            rows = tx.execute("SELECT * FROM frames WHERE rig = ? AND night = ? AND image_type = 'light' AND coalesce(target, '') = ?",
                              (rig, night, object_header or "")).fetchall()
        previous = sorted({r["hub_target_id"] for r in rows if r["hub_target_id"] is not None and r["hub_target_id"] != target_id})
        for row in rows:
            status = "valid" if row["status"] == "held" else row["status"]
            tx.execute("UPDATE frames SET hub_target_id = ?, assignment_source = 'manual', status = ? WHERE id = ?", (target_id, status, row["id"]))
            if config.hub.enabled and source != "hub":
                enqueue_frame(tx, config, row["id"])
        groups = {(r["rig"], r["night"], r["target"]) for r in rows}
        for g_rig, g_night, g_object in groups:
            still_held = tx.execute("SELECT count(*) AS n FROM frames WHERE rig = ? AND night = ? AND coalesce(target, '') = ? AND status = 'held'",
                                    (g_rig, g_night, g_object or "")).fetchone()["n"]
            if still_held == 0:
                resolve_issue(tx, config, unresolved_fingerprint(g_rig, g_night, g_object), resolution=f"assigned:{target_id}")
        if rows:
            tx.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES ('replan', ?, ?, ?)",
                       (json.dumps({"targets": [target_id, *previous], "nights": sorted({r["night"] for r in rows})}), source, now_iso()))
        return len(rows)


def adopt_hub_target(catalog: Catalog, sha256: str, target_id: int | None) -> None:
    """The Hub answered with a different target (a manual assignment made
    there): adopt it (belt-and-braces with the assign_frames command)."""
    row = catalog.one("SELECT hub_target_id FROM frames WHERE sha256 = ?", (sha256,))
    if row and target_id is not None and row["hub_target_id"] != target_id:
        catalog.execute("UPDATE frames SET hub_target_id = ?, assignment_source = 'manual', "
                        "status = CASE status WHEN 'held' THEN 'valid' ELSE status END WHERE sha256 = ?", (target_id, sha256))
    catalog.execute("UPDATE frames SET hub_synced_at = ? WHERE sha256 = ?", (now_iso(), sha256))


def _int(value: Any) -> int | None:
    return int(value) if value is not None else None
