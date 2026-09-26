"""Blobs and replicas in the catalog (SPEC §6.3, §7.1): the catalog says
*what* data exists, replicas say *where* it currently is."""
from __future__ import annotations

import sqlite3
from typing import Iterable

from altair.catalog.db import now_iso

DURABLE = {"nas": 1, "s3": 1}


def ensure_location(tx: sqlite3.Connection, name: str, kind: str = "fs", *, durable: bool | None = None, read_only: bool = False) -> None:
    durable = DURABLE.get(name, 0) if durable is None else int(durable)
    tx.execute("INSERT OR IGNORE INTO locations(name, kind, durable, read_only) VALUES (?, ?, ?, ?)", (name, kind, durable, int(read_only)))


def add_blob(tx: sqlite3.Connection, sha256: str, size: int, data_class: str, logical_path: str, origin_rig: str | None = None) -> bool:
    """Register a blob once. Returns True when it is new."""
    return tx.execute("INSERT OR IGNORE INTO blobs(sha256, size_bytes, data_class, logical_path, origin_rig, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (sha256, size, data_class, logical_path, origin_rig, now_iso())).rowcount == 1


def set_replica(tx: sqlite3.Connection, sha256: str, location: str, uri: str, *, state: str = "present", kind: str | None = None,
                verified_at: str | None = None, method: str | None = "sha256_full", storage_class: str | None = None,
                version_id: str | None = None) -> None:
    ensure_location(tx, location, kind or ("s3" if location == "s3" else "fs"), read_only=location.startswith("external:"))
    now = now_iso()
    tx.execute(
        "INSERT INTO replicas(sha256, location, uri, state, storage_class, verified_at, verify_method, last_seen_at, version_id, missing_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL) ON CONFLICT(sha256, location) DO UPDATE SET uri = excluded.uri, state = excluded.state, "
        "storage_class = coalesce(excluded.storage_class, replicas.storage_class), verified_at = excluded.verified_at, "
        "verify_method = excluded.verify_method, last_seen_at = excluded.last_seen_at, "
        "version_id = coalesce(excluded.version_id, replicas.version_id), missing_reason = NULL",
        (sha256, location, uri, state, storage_class, verified_at or (now if state == "present" else None), method, now, version_id),
    )


def mark_missing(tx: sqlite3.Connection, sha256: str, location: str, reason: str) -> None:
    tx.execute("UPDATE replicas SET state = 'missing', missing_reason = ?, verified_at = NULL WHERE sha256 = ? AND location = ?",
               (reason, sha256, location))


def mark_corrupt(tx: sqlite3.Connection, sha256: str, location: str) -> None:
    tx.execute("UPDATE replicas SET state = 'corrupt', verified_at = NULL WHERE sha256 = ? AND location = ?", (sha256, location))


def replicas(conn: sqlite3.Connection, sha256: str) -> dict[str, sqlite3.Row]:
    return {r["location"]: r for r in conn.execute("SELECT * FROM replicas WHERE sha256 = ?", (sha256,))}


def verified(row: sqlite3.Row | None) -> bool:
    return bool(row) and row["state"] in ("present", "archived_cold", "restored") and row["verified_at"] is not None


def verified_locations(conn: sqlite3.Connection, sha256: str, exclude: Iterable[str] = ()) -> list[str]:
    skip = set(exclude)
    return [loc for loc, row in replicas(conn, sha256).items() if loc not in skip and verified(row)]


def blob(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM blobs WHERE sha256 = ?", (sha256,)).fetchone()
