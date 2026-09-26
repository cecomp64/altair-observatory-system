"""Ingest of a collected, verified frame (SPEC §6.2): canonical fields,
required-field validation, the rig cross-check, Hub target resolution, and
registration of the blob, its replicas and the frame in one transaction."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from altair import frames as frame_ops
from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.hub import resolver
from altair.hub.config_sync import HubConfig
from altair.ingest.headers import normalize
from altair.issues import raise_issue

# SPEC §6.2 "Required fields" (gain/offset are optional: not every camera reports them).
REQUIRED = {
    "light": ["telescope", "camera", "filter", "target", "exposure", "binning", "focal_length", "date_obs"],
    "flat": ["telescope", "camera", "filter", "exposure", "binning", "focal_length", "date_obs"],
    "dark": ["camera", "exposure", "binning", "date_obs"],
    "darkflat": ["camera", "exposure", "binning", "date_obs"],
    "bias": ["camera", "binning", "date_obs"],
}


@dataclass
class Ingested:
    frame_id: int
    created: bool
    fields: dict[str, Any]
    status: str
    data_class: str


def data_class_for(image_type: str | None) -> str:
    return "raw_light" if image_type == "light" else "raw_calibration"


def missing_fields(fields: dict[str, Any], config: AltairConfig, rig_name: str) -> list[str]:
    rig = config.rigs[rig_name]
    image_type = fields.get("image_type")
    if image_type is None:
        return ["image_type"]
    missing = [name for name in REQUIRED[image_type] if fields.get(name) in (None, "")]
    if image_type in ("dark", "darkflat"):
        camera = config.camera(fields.get("camera") or rig.camera)
        if (camera is None or camera.cooled) and fields.get("sensor_temp") is None:
            missing.append("sensor_temp")
    if image_type in ("light", "flat") and rig.rotator.present and rig.rotator.require_on_lights_and_flats and fields.get("rotator_pos") is None:
        missing.append("rotator_pos")
    return missing


def ingest(catalog: Catalog, config: AltairConfig, hub_config: HubConfig | None, *, rig: str, header: dict[str, Any], sha256: str,
           size: int, logical_path: str, file_name: str, replicas: list[tuple[str, str]], origin: str = "collect") -> Ingested:
    """Register one frame. ``replicas`` are (location, uri) pairs already
    verified (e.g. the rig original and the NAS copy)."""
    rig_cfg = config.rigs[rig]
    fields = normalize(header, config, rig_cfg, file_name)
    image_type = fields["image_type"]
    status, reason, issue = None, None, None

    missing = missing_fields(fields, config, rig) if image_type else ["image_type"]
    if image_type is None or fields["date_obs"] is None:
        # Nothing can be done with it without a type and a night; keep the
        # blob so a config fix and a re-ingest can pick it up.
        fields["image_type"] = fields["image_type"] or "light"
        fields["night"] = fields["night"] or "unknown"
        fields["date_obs"] = fields["date_obs"] or _epoch()
    if missing:
        status, reason = "invalid", "missing " + ", ".join(missing)
        issue = ("HEADER_INCOMPLETE", f"HEADER_INCOMPLETE:{rig}:{fields['night']}:{','.join(missing)}",
                 f"Frames on {rig} for the night of {fields['night']} lack {', '.join(missing)} (e.g. {file_name}). "
                 "Fix the NINA headers or the header_mapping/aliases in altair.yaml, then re-ingest with `altair rerun`.")
    elif (fields["telescope"], fields["camera"]) != (rig_cfg.telescope, rig_cfg.camera) and image_type in ("light", "flat"):
        status, reason = "invalid", f"headers name {fields['telescope']}/{fields['camera']}, not rig {rig}"
        issue = ("UNKNOWN_RIG", f"UNKNOWN_RIG:{rig}:{fields['telescope']}:{fields['camera']}",
                 f"{file_name} was collected from rig {rig} but its headers name telescope {fields['telescope']!r} and camera "
                 f"{fields['camera']!r} (the rig is {rig_cfg.telescope}/{rig_cfg.camera}). Fix the aliases in altair.yaml.")
    elif image_type in ("dark", "darkflat", "bias") and fields["camera"] != rig_cfg.camera:
        status, reason = "invalid", f"camera {fields['camera']} is not rig {rig}'s {rig_cfg.camera}"
        issue = ("UNKNOWN_RIG", f"UNKNOWN_RIG:{rig}:camera:{fields['camera']}",
                 f"{file_name} was collected from rig {rig} but its camera is {fields['camera']!r}, not {rig_cfg.camera!r}.")

    resolution = None
    if image_type == "light" and status is None and hub_config and rig_cfg.hub:
        telescope, train = rig_cfg.hub.telescope, rig_cfg.hub.optical_train
        fields["filter"] = hub_config.canonical_filter(telescope, train, fields["raw_filter"]) or fields["filter"]
        r = resolver.resolve(object_header=fields["target"], ra=fields["ra_deg"], dec=fields["dec_deg"], telescope=telescope,
                             optical_train=train, config=hub_config, rules=config.hub.resolve)
        resolution = (r.target_id, r.source)
    elif image_type == "light" and status is None and config.hub.enabled and config.hub.require_target_link and hub_config is None:
        resolution = None   # no cached Hub config yet: held until one arrives (SPEC §17.2)

    def on_created(tx: sqlite3.Connection, frame_id: int) -> None:
        if issue:
            kind, fingerprint, message = issue
            raise_issue(tx, config, kind=kind, severity="blocking", fingerprint=fingerprint, message=message,
                        scope={"rig": rig, "night": str(fields["night"]), "frame_ids": [frame_id], "file": file_name})
        _touch_collection(tx, rig, str(fields["night"]))

    location, uri = replicas[0]
    frame_id, created = frame_ops.register(
        catalog, config, sha256=sha256, size=size, logical_path=logical_path, data_class=data_class_for(image_type),
        location=location, uri=uri, extra_replicas=replicas[1:], fields=fields, rig=rig, origin=origin, file_name=file_name,
        headers=header, resolution=resolution, status=status, status_reason=reason, on_created=on_created,
    )
    row = catalog.one("SELECT status FROM frames WHERE id = ?", (frame_id,))
    return Ingested(frame_id, created, fields, row["status"], data_class_for(image_type))


def _touch_collection(tx: sqlite3.Connection, rig: str, night: str) -> None:
    """Open the rig-night on its first frame, and remember when the newest
    frame arrived (quiescence, SPEC §6.1). A frame for a closed night is a
    late frame; the collector re-closes the night (SPEC §7.3)."""
    tx.execute(
        "INSERT INTO collections(rig, night, state, opened_at, last_frame_at) VALUES (?, ?, 'open', ?, ?) "
        "ON CONFLICT(rig, night) DO UPDATE SET last_frame_at = max(coalesce(collections.last_frame_at, ''), excluded.last_frame_at)",
        (rig, night, now_iso(), now_iso()),
    )


def _epoch():
    from datetime import datetime, timezone

    return datetime(1970, 1, 1, tzinfo=timezone.utc)
