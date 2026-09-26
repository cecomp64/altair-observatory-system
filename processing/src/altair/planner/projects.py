"""Processing projects (SPEC §2, §6.4 step 4a). A linked project is one Hub
target on one rig; a legacy one is (target text, telescope, camera) for
standalone sites. Its logical path is fixed when it is created, so renaming
a target in the Hub never renames archived paths (§6.6)."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from altair.catalog.db import now_iso
from altair.config import AltairConfig
from altair.hub.config_sync import HubConfig


def slug(text: str | None) -> str:
    out = re.sub(r"[^A-Za-z0-9]+", "_", (text or "").strip()).strip("_")
    return out[:60] or "untitled"


def default_settings(config: AltairConfig) -> dict[str, Any]:
    mn = config.multi_night
    return {
        "multi_night": {"enabled": mn.enabled, "mode": mn.mode},
        "reference_filter": None,
        "drizzle_scale": 1,
        "keep_calibrated_frames": True,
        "pin_to_nas": False,
        "min_lights_per_stack": config.triggers.min_lights_per_stack,
        "max_fwhm_ratio_to_project_median": mn.max_fwhm_ratio_to_project_median,
        "wbpp_profile": config.night_processing.wbpp_profile,
    }


def effective_settings(config: AltairConfig, hub_settings: dict[str, Any] | None) -> dict[str, Any]:
    """Altair defaults ← the Hub's merged project/target settings."""
    out = default_settings(config)
    for key, value in (hub_settings or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **{k: v for k, v in value.items() if v is not None}}
        elif value is not None:
            out[key] = value
    return out


def project_for(tx: sqlite3.Connection, config: AltairConfig, hub_config: HubConfig | None, *, rig: str,
                hub_target_id: int | None = None, target_text: str | None = None) -> sqlite3.Row:
    """Find or create the project for (hub target, rig) or (target text, rig),
    refreshing its effective settings. The caller holds the transaction."""
    rig_cfg = config.rigs[rig]
    if hub_target_id is not None:
        row = tx.execute("SELECT * FROM projects WHERE hub_target_id = ? AND rig = ?", (hub_target_id, rig)).fetchone()
        target = (hub_config.targets.get(hub_target_id) if hub_config else None) or {}
        settings = effective_settings(config, target.get("processing_settings"))
        if row is None:
            path = f"projects/{rig}/T{hub_target_id}_{slug(target.get('name'))}"
            tx.execute("INSERT INTO projects(target, telescope, camera, rig, hub_target_id, hub_project_id, settings_json, multi_night_mode, path, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (f"T{hub_target_id}", rig_cfg.telescope, rig_cfg.camera, rig, hub_target_id, target.get("project_id"),
                        json.dumps(settings, sort_keys=True), settings["multi_night"]["mode"], path, now_iso()))
        else:
            tx.execute("UPDATE projects SET settings_json = ?, multi_night_mode = ?, hub_project_id = coalesce(?, hub_project_id) WHERE id = ?",
                       (json.dumps(settings, sort_keys=True), settings["multi_night"]["mode"], target.get("project_id"), row["id"]))
        return tx.execute("SELECT * FROM projects WHERE hub_target_id = ? AND rig = ?", (hub_target_id, rig)).fetchone()
    row = tx.execute("SELECT * FROM projects WHERE target = ? AND telescope = ? AND camera = ?",
                     (target_text, rig_cfg.telescope, rig_cfg.camera)).fetchone()
    if row is None:
        settings = default_settings(config)
        tx.execute("INSERT INTO projects(target, telescope, camera, rig, settings_json, multi_night_mode, path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (target_text, rig_cfg.telescope, rig_cfg.camera, rig, json.dumps(settings, sort_keys=True), settings["multi_night"]["mode"],
                    f"projects/{rig}/{slug(target_text)}", now_iso()))
        row = tx.execute("SELECT * FROM projects WHERE target = ? AND telescope = ? AND camera = ?",
                         (target_text, rig_cfg.telescope, rig_cfg.camera)).fetchone()
    return row


def set_mode(tx: sqlite3.Connection, project_id: int, mode: str) -> None:
    """The multi-night mode lives in the project's effective settings (from
    the Hub target, or local defaults); ``multi_night_mode`` mirrors it. A
    mode change updates both, so it applies before the next config pull."""
    row = tx.execute("SELECT settings_json FROM projects WHERE id = ?", (project_id,)).fetchone()
    current = json.loads(row["settings_json"]) if row and row["settings_json"] else {}
    current.setdefault("multi_night", {})["mode"] = mode
    tx.execute("UPDATE projects SET settings_json = ?, multi_night_mode = ? WHERE id = ?", (json.dumps(current, sort_keys=True), mode, project_id))


def settings(project: sqlite3.Row, config: AltairConfig) -> dict[str, Any]:
    return json.loads(project["settings_json"]) if project["settings_json"] else default_settings(config)


def project_label(project: sqlite3.Row) -> str:
    return f"T{project['hub_target_id']}" if project["hub_target_id"] is not None else project["target"]
