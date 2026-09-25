"""Reconciliation (SPEC §17.6): compare each closed night's local digest
with the Hub's and re-queue the night's frames on a mismatch."""
from __future__ import annotations

from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.hub.client import HubClient
from altair.hub.reporters import enqueue_frame


def local_digest(catalog: Catalog, rig: str, night: str) -> dict:
    rows = catalog.query("SELECT sha256, image_type FROM frames WHERE rig = ? AND night = ?", (rig, night))
    xor = bytes(32)
    by_type: dict[str, int] = {}
    for row in rows:
        xor = bytes(a ^ b for a, b in zip(xor, bytes.fromhex(row["sha256"])))
        by_type[row["image_type"]] = by_type.get(row["image_type"], 0) + 1
    return {"frame_count": len(rows), "sha256_xor": xor.hex(), "by_type": by_type}


def reconcile(catalog: Catalog, config: AltairConfig, client: HubClient, *, rig: str | None = None, night: str | None = None) -> list[dict]:
    """Returns one entry per night checked: {rig, night, ok, requeued}."""
    sql = "SELECT rig, night FROM collections WHERE state = 'closed'"
    params: list[str] = []
    if rig:
        sql += " AND rig = ?"
        params.append(rig)
    if night:
        sql += " AND night = ?"
        params.append(night)
    report = []
    for row in catalog.query(sql + " ORDER BY night, rig", tuple(params)):
        rig_cfg = config.rigs.get(row["rig"])
        if not rig_cfg or not rig_cfg.hub:
            continue
        ours = local_digest(catalog, row["rig"], row["night"])
        theirs = client.night_digest(rig_cfg.hub.optical_train, row["night"])
        ok = ours == {**theirs, "by_type": {k: v for k, v in theirs.get("by_type", {}).items() if v}}
        requeued = 0
        if not ok:
            with catalog.transaction() as tx:
                for frame in tx.execute("SELECT id FROM frames WHERE rig = ? AND night = ?", (row["rig"], row["night"])).fetchall():
                    enqueue_frame(tx, config, frame["id"])
                    requeued += 1
        report.append({"rig": row["rig"], "night": row["night"], "ok": ok, "requeued": requeued})
    return report
