"""Equipment events (SPEC §8.3): a reducer, filter or camera change splits
flat validity. They come from the Hub (the ``equipment_event`` command) or,
standalone or before the Hub knows, from `altair equipment log`."""
from __future__ import annotations

import json

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.issues import resolve_issue

KINDS = ("sensor_cleaned", "filter_changed", "camera_rotated_manually", "reducer_changed", "collimated", "other")


class EquipmentError(Exception):
    pass


def log_event(catalog: Catalog, config: AltairConfig, *, rig: str, kind: str, at: str, filter_: str | None = None,
              note: str | None = None) -> int:
    """Record the event, confirm a matching EQUIPMENT_CHANGE_SUSPECTED, and
    re-plan the nights whose flat matching it can change."""
    if rig not in config.rigs:
        raise EquipmentError(f"unknown rig {rig!r}")
    if kind not in KINDS:
        raise EquipmentError(f"kind must be one of {', '.join(KINDS)}")
    with catalog.transaction() as tx:
        event_id = tx.execute("INSERT INTO equipment_events(at, rig, kind, filter, note) VALUES (?, ?, ?, ?, ?)",
                              (at, rig, kind, filter_, note)).lastrowid
        for issue in tx.execute("SELECT fingerprint FROM issues WHERE status = 'open' AND kind = 'EQUIPMENT_CHANGE_SUSPECTED' AND fingerprint LIKE ?",
                                (f"EQUIPMENT_CHANGE_SUSPECTED:{rig}:%",)).fetchall():
            resolve_issue(tx, config, issue["fingerprint"], resolution=f"confirmed:{kind}")
        tx.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES ('equipment_event', ?, 'cli', ?)",
                   (json.dumps({"rig": rig, "event_id": event_id, "at": at}), now_iso()))
    return event_id


def events(catalog: Catalog, rig: str | None = None) -> list[dict]:
    rows = catalog.query("SELECT * FROM equipment_events" + (" WHERE rig = ?" if rig else "") + " ORDER BY at", (rig,) if rig else ())
    return [dict(r) for r in rows]
