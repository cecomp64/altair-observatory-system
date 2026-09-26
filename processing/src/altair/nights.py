"""Night collection state (SPEC §6.1, §7.3): opening and closing rig-nights."""
from __future__ import annotations

import json

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.hub.reporters import enqueue_night

SESSION_END = ("session_end", "session_end_marker")


def collector_managed(config: AltairConfig, rig: str) -> bool:
    """The collector owns closing for rigs it pulls from: it does a final scan
    and writes the collection manifest before the night counts as closed."""
    rig_cfg = config.rigs.get(rig)
    return bool(rig_cfg and rig_cfg.raw_root and config.storage.nas and config.storage.nas.root)


def request_close(catalog: Catalog, config: AltairConfig, *, rig: str, night: str, closed_by: str, at: str | None = None) -> str:
    """A trigger fired (Hub night_ready, session-end marker, quiescence,
    schedule, or by hand). Collector-managed rigs go to ``closing`` and the
    collector finishes the close; other rigs close now. Returns the state."""
    if not collector_managed(config, rig):
        close(catalog, config, rig=rig, night=night, closed_by=closed_by, at=at)
        return "closed"
    with catalog.transaction() as tx:
        row = tx.execute("SELECT state FROM collections WHERE rig = ? AND night = ?", (rig, night)).fetchone()
        if row and row["state"] in ("closing", "closed"):
            return row["state"]
        tx.execute(
            "INSERT INTO collections(rig, night, state, closed_by, opened_at, session_end_at) VALUES (?, ?, 'closing', ?, ?, ?) "
            "ON CONFLICT(rig, night) DO UPDATE SET state = 'closing', closed_by = excluded.closed_by, "
            "session_end_at = coalesce(excluded.session_end_at, collections.session_end_at)",
            (rig, night, closed_by, now_iso(), at if closed_by in SESSION_END else None),
        )
        if config.hub.enabled:
            enqueue_night(tx, config, rig, night)
    return "closing"


def close(catalog: Catalog, config: AltairConfig, *, rig: str, night: str, closed_by: str, at: str | None = None,
          manifest_sha256: str | None = None, reclose: bool = False) -> None:
    """Close a rig-night, report it, and ask the planner to plan it.
    Idempotent; ``reclose`` re-closes after a late frame (new manifest,
    re-plan) keeping what closed it first."""
    with catalog.transaction() as tx:
        row = tx.execute("SELECT state, closed_by FROM collections WHERE rig = ? AND night = ?", (rig, night)).fetchone()
        if row and row["state"] == "closed" and not reclose:
            return
        if row and row["closed_by"] and (reclose or row["state"] == "closing"):
            closed_by = row["closed_by"]
        n_files, total_bytes = tx.execute(
            "SELECT count(*) AS n, coalesce(sum(b.size_bytes), 0) AS bytes FROM frames f JOIN blobs b ON b.sha256 = f.sha256 "
            "WHERE f.rig = ? AND f.night = ?", (rig, night)).fetchone()
        tx.execute(
            "INSERT INTO collections(rig, night, state, closed_by, n_files, total_bytes, manifest_sha256, opened_at, closed_at, session_end_at) "
            "VALUES (?, ?, 'closed', ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(rig, night) DO UPDATE SET state = 'closed', "
            "closed_by = excluded.closed_by, n_files = excluded.n_files, total_bytes = excluded.total_bytes, "
            "manifest_sha256 = coalesce(excluded.manifest_sha256, collections.manifest_sha256), closed_at = excluded.closed_at, "
            "session_end_at = coalesce(excluded.session_end_at, collections.session_end_at)",
            (rig, night, closed_by, n_files, total_bytes, manifest_sha256, now_iso(), now_iso(), at if closed_by in SESSION_END else None),
        )
        tx.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES ('night_ready', ?, ?, ?)",
                   (json.dumps({"rig": rig, "night": night, "closed_by": closed_by, "reclose": reclose}), closed_by, now_iso()))
        if config.hub.enabled:
            enqueue_night(tx, config, rig, night)
