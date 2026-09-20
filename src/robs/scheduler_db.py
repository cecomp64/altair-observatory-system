"""Adapter around NINA Target Scheduler's SQLite database.

Read `scheduler_schema.py` first — the column names here are best
effort and may need adjusting for your installed plugin version.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import scheduler_schema as schema


class SchedulerSchemaError(RuntimeError):
    """The scheduler DB doesn't look like what scheduler_schema.py expects."""


@contextmanager
def open_scheduler_db(db_path: Path):
    if not db_path.exists():
        raise SchedulerSchemaError(
            f"Target Scheduler database not found at {db_path}. "
            "Is NINA installed with the Target Scheduler plugin on this machine?"
        )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_schema_compatible(conn: sqlite3.Connection) -> None:
    """Verify every table/column scheduler_schema.py expects actually exists.

    Fails loudly and specifically rather than silently corrupting or
    half-writing NINA's database.
    """
    for table, columns in schema.ALL_TABLES.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            raise SchedulerSchemaError(
                f"Table '{table}' not found in Target Scheduler database. "
                "Your plugin version's schema may differ — see scheduler_schema.py."
            )
        existing = {row["name"] for row in rows}
        missing = {col for col in columns.values() if col not in existing}
        if missing:
            raise SchedulerSchemaError(
                f"Table '{table}' is missing expected column(s) {sorted(missing)}. "
                "Your plugin version's schema may differ — see scheduler_schema.py."
            )


def get_or_create_project(conn: sqlite3.Connection, profile_id: str) -> int:
    cols = schema.PROJECT_COLUMNS
    row = conn.execute(
        f"SELECT {cols['id']} FROM {schema.PROJECT_TABLE} "
        f"WHERE {cols['name']} = ? AND {cols['profile_id']} = ?",
        (schema.MANAGED_PROJECT_NAME, profile_id),
    ).fetchone()
    if row:
        return row[cols["id"]]

    cursor = conn.execute(
        f"INSERT INTO {schema.PROJECT_TABLE} "
        f"({cols['profile_id']}, {cols['name']}, {cols['description']}, {cols['state']}, {cols['priority']}, {cols['create_date']}) "
        f"VALUES (?, ?, ?, ?, ?, strftime('%s','now'))",
        (profile_id, schema.MANAGED_PROJECT_NAME, "Managed by remote-observatory-worker", 1, 0),
    )
    return cursor.lastrowid


def get_or_create_exposure_template(
    conn: sqlite3.Connection, profile_id: str, filter_name: str, default_exposure_seconds: int
) -> int:
    cols = schema.EXPOSURE_TEMPLATE_COLUMNS
    row = conn.execute(
        f"SELECT {cols['id']} FROM {schema.EXPOSURE_TEMPLATE_TABLE} "
        f"WHERE {cols['profile_id']} = ? AND {cols['filter_name']} = ?",
        (profile_id, filter_name),
    ).fetchone()
    if row:
        return row[cols["id"]]

    cursor = conn.execute(
        f"INSERT INTO {schema.EXPOSURE_TEMPLATE_TABLE} "
        f"({cols['profile_id']}, {cols['name']}, {cols['filter_name']}, {cols['default_exposure']}) "
        f"VALUES (?, ?, ?, ?)",
        (profile_id, filter_name, filter_name, default_exposure_seconds),
    )
    return cursor.lastrowid


def upsert_target(conn: sqlite3.Connection, project_id: int, name: str, ra_deg: float, dec_deg: float) -> int:
    cols = schema.TARGET_COLUMNS
    row = conn.execute(
        f"SELECT {cols['id']} FROM {schema.TARGET_TABLE} WHERE {cols['project_id']} = ? AND {cols['name']} = ?",
        (project_id, name),
    ).fetchone()
    if row:
        target_id = row[cols["id"]]
        conn.execute(
            f"UPDATE {schema.TARGET_TABLE} SET {cols['ra']} = ?, {cols['dec']} = ?, {cols['enabled']} = 1 "
            f"WHERE {cols['id']} = ?",
            (ra_deg, dec_deg, target_id),
        )
        return target_id

    cursor = conn.execute(
        f"INSERT INTO {schema.TARGET_TABLE} "
        f"({cols['project_id']}, {cols['name']}, {cols['ra']}, {cols['dec']}, {cols['enabled']}) "
        f"VALUES (?, ?, ?, ?, 1)",
        (project_id, name, ra_deg, dec_deg),
    )
    return cursor.lastrowid


def upsert_exposure_plan(conn: sqlite3.Connection, target_id: int, exposure_template_id: int, desired: int) -> int:
    cols = schema.EXPOSURE_PLAN_COLUMNS
    row = conn.execute(
        f"SELECT {cols['id']} FROM {schema.EXPOSURE_PLAN_TABLE} "
        f"WHERE {cols['target_id']} = ? AND {cols['exposure_template_id']} = ?",
        (target_id, exposure_template_id),
    ).fetchone()
    if row:
        plan_id = row[cols["id"]]
        conn.execute(
            f"UPDATE {schema.EXPOSURE_PLAN_TABLE} SET {cols['desired']} = ? WHERE {cols['id']} = ?",
            (desired, plan_id),
        )
        return plan_id

    cursor = conn.execute(
        f"INSERT INTO {schema.EXPOSURE_PLAN_TABLE} "
        f"({cols['target_id']}, {cols['exposure_template_id']}, {cols['desired']}, {cols['accepted']}) "
        f"VALUES (?, ?, ?, 0)",
        (target_id, exposure_template_id, desired),
    )
    return cursor.lastrowid


def read_accepted_count(conn: sqlite3.Connection, exposure_plan_id: int) -> int:
    cols = schema.EXPOSURE_PLAN_COLUMNS
    row = conn.execute(
        f"SELECT {cols['accepted']} FROM {schema.EXPOSURE_PLAN_TABLE} WHERE {cols['id']} = ?",
        (exposure_plan_id,),
    ).fetchone()
    return int(row[cols["accepted"]]) if row else 0


def disable_target(conn: sqlite3.Connection, target_id: int) -> None:
    cols = schema.TARGET_COLUMNS
    conn.execute(f"UPDATE {schema.TARGET_TABLE} SET {cols['enabled']} = 0 WHERE {cols['id']} = ?", (target_id,))


def delete_target(conn: sqlite3.Connection, target_id: int) -> None:
    plan_cols = schema.EXPOSURE_PLAN_COLUMNS
    target_cols = schema.TARGET_COLUMNS
    conn.execute(f"DELETE FROM {schema.EXPOSURE_PLAN_TABLE} WHERE {plan_cols['target_id']} = ?", (target_id,))
    conn.execute(f"DELETE FROM {schema.TARGET_TABLE} WHERE {target_cols['id']} = ?", (target_id,))


def new_profile_id() -> str:
    """Handy for local/dev testing when a real NINA profile GUID isn't at hand."""
    return str(uuid.uuid4())
