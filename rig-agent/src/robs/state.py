"""Local id-mapping database.

We deliberately do *not* try to stash a "Rails target id" column onto
NINA's Target Scheduler tables — that plugin's schema isn't ours to
extend safely across upgrades. Instead we keep a small side SQLite
database (one per telescope) mapping:

    rails_target_id  <->  scheduler_target_id, scheduler_exposure_plan_id

This makes every sync idempotent (re-running `roof-open` just upserts)
and lets `sync-progress` / `cleanup` find their way back to the right
NINA rows without touching the plugin's own schema.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS target_links (
    rails_target_id INTEGER PRIMARY KEY,
    scheduler_target_id INTEGER NOT NULL,
    scheduler_project_id INTEGER NOT NULL,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS exposure_plan_links (
    rails_exposure_plan_id INTEGER PRIMARY KEY,
    rails_target_id INTEGER NOT NULL,
    scheduler_exposure_plan_id INTEGER NOT NULL,
    filter TEXT NOT NULL,
    FOREIGN KEY (rails_target_id) REFERENCES target_links (rails_target_id)
);
"""


def state_db_path_for(scheduler_db_path: Path) -> Path:
    """Default location: next to the scheduler DB, e.g. `robs_state.sqlite`."""
    return scheduler_db_path.parent / "robs_state.sqlite"


@contextmanager
def open_state_db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def link_target(conn: sqlite3.Connection, rails_target_id: int, scheduler_target_id: int, scheduler_project_id: int) -> None:
    conn.execute(
        """
        INSERT INTO target_links (rails_target_id, scheduler_target_id, scheduler_project_id, last_synced_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(rails_target_id) DO UPDATE SET
            scheduler_target_id = excluded.scheduler_target_id,
            scheduler_project_id = excluded.scheduler_project_id,
            last_synced_at = excluded.last_synced_at
        """,
        (rails_target_id, scheduler_target_id, scheduler_project_id),
    )


def link_exposure_plan(
    conn: sqlite3.Connection,
    rails_exposure_plan_id: int,
    rails_target_id: int,
    scheduler_exposure_plan_id: int,
    filter: str,
) -> None:
    conn.execute(
        """
        INSERT INTO exposure_plan_links (rails_exposure_plan_id, rails_target_id, scheduler_exposure_plan_id, filter)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(rails_exposure_plan_id) DO UPDATE SET
            scheduler_exposure_plan_id = excluded.scheduler_exposure_plan_id,
            filter = excluded.filter
        """,
        (rails_exposure_plan_id, rails_target_id, scheduler_exposure_plan_id, filter),
    )


def find_target_link(conn: sqlite3.Connection, rails_target_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM target_links WHERE rails_target_id = ?", (rails_target_id,)
    ).fetchone()


def exposure_plan_links_for_target(conn: sqlite3.Connection, rails_target_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM exposure_plan_links WHERE rails_target_id = ?", (rails_target_id,)
    ).fetchall()


def all_target_links(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM target_links").fetchall()


def remove_target_link(conn: sqlite3.Connection, rails_target_id: int) -> None:
    conn.execute("DELETE FROM exposure_plan_links WHERE rails_target_id = ?", (rails_target_id,))
    conn.execute("DELETE FROM target_links WHERE rails_target_id = ?", (rails_target_id,))
