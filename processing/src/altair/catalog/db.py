"""The SQLite catalog (SPEC §6.3), in WAL mode.

Writes that must reach the Hub enqueue an outbox item inside the same
transaction (``with catalog.transaction() as tx:``), so nothing is lost
between "Altair knows" and "the Hub knows" (SPEC §17.3).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Iterator

SCHEMA_VERSION = "9"

# Columns added after a table first shipped: (table, column, declaration).
# CREATE TABLE IF NOT EXISTS leaves an older catalog's tables as they were, so
# these are added in place when missing.
ADDED_COLUMNS = [
    ("replicas", "version_id", "TEXT"),
    ("replicas", "missing_reason", "TEXT"),
    ("collections", "last_frame_at", "TEXT"),
]


_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def now_iso() -> str:
    return _clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def use_clock(clock: Callable[[], datetime] | None) -> None:
    """Timestamps written to the catalog come from ``clock`` (tests and replays
    drive time; None restores the wall clock)."""
    global _clock
    _clock = clock or (lambda: datetime.now(timezone.utc))


class Catalog:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(resources.files("altair.catalog").joinpath("schema.sql").read_text())
        for table, column, declaration in ADDED_COLUMNS:
            columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        self.conn.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', ?)", (SCHEMA_VERSION,))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    # Small key/value store for sync state (last poll times, etc.).
    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM hub_state WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        self.execute("INSERT OR REPLACE INTO hub_state(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def close(self) -> None:
        self.conn.close()
