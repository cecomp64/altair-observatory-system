"""Night collection state (SPEC §6.1, §7.3): opening and closing rig-nights."""
from __future__ import annotations

import json

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.hub.reporters import enqueue_night


def close(catalog: Catalog, config: AltairConfig, *, rig: str, night: str, closed_by: str, at: str | None = None) -> None:
    """Close a rig-night (session end, marker, quiescence, schedule or by
    hand), report it, and ask the planner to plan it. Idempotent."""
    with catalog.transaction() as tx:
        row = tx.execute("SELECT state FROM collections WHERE rig = ? AND night = ?", (rig, night)).fetchone()
        if row and row["state"] == "closed":
            return
        n_files = tx.execute("SELECT count(*) AS n FROM frames WHERE rig = ? AND night = ?", (rig, night)).fetchone()["n"]
        tx.execute(
            "INSERT INTO collections(rig, night, state, closed_by, n_files, opened_at, closed_at, session_end_at) "
            "VALUES (?, ?, 'closed', ?, ?, ?, ?, ?) ON CONFLICT(rig, night) DO UPDATE SET state = 'closed', "
            "closed_by = excluded.closed_by, n_files = excluded.n_files, closed_at = excluded.closed_at, "
            "session_end_at = coalesce(excluded.session_end_at, collections.session_end_at)",
            (rig, night, closed_by, n_files, now_iso(), now_iso(), at if closed_by in ("session_end", "session_end_marker") else None),
        )
        tx.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES ('night_ready', ?, ?, ?)",
                   (json.dumps({"rig": rig, "night": night, "closed_by": closed_by}), closed_by, now_iso()))
        if config.hub.enabled:
            enqueue_night(tx, config, rig, night)
