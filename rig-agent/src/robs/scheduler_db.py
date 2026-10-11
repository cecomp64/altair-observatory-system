"""Adapter around NINA Target Scheduler's SQLite database.

Read `scheduler_schema.py` first — the column names and defaults there
were checked against Target Scheduler 5.9.6 and may need adjusting for
another plugin version.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from . import scheduler_schema as schema


class SchedulerSchemaError(RuntimeError):
    """The scheduler DB doesn't look like what scheduler_schema.py expects."""


class SchedulerBackupError(RuntimeError):
    """The scheduler DB couldn't be backed up, so it must not be changed."""


# Backups go in this folder beside the database; the newest BACKUPS_KEPT stay.
BACKUP_DIR_NAME = "robs_backups"
BACKUPS_KEPT = 14


def backup_scheduler_db(db_path: Path) -> Path:
    """Copies the database before robs changes it and returns the copy's path.

    Uses SQLite's online backup, which gives a consistent copy while NINA has
    the database open.
    """
    folder = db_path.parent / BACKUP_DIR_NAME
    backup_path = folder / f"{db_path.stem}-{datetime.now().strftime('%Y%m%dT%H%M%S-%f')}{db_path.suffix}"
    try:
        folder.mkdir(exist_ok=True)
        with closing(sqlite3.connect(str(db_path))) as source, closing(sqlite3.connect(str(backup_path))) as copy:
            source.backup(copy)
    except (sqlite3.Error, OSError) as e:
        backup_path.unlink(missing_ok=True)
        raise SchedulerBackupError(f"Couldn't back up {db_path} to {backup_path}: {e}") from e

    for old in sorted(folder.glob(f"{db_path.stem}-*{db_path.suffix}"))[:-BACKUPS_KEPT]:
        old.unlink(missing_ok=True)
    return backup_path


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
        existing = {row["name"].lower() for row in rows}  # SQLite column names ignore case
        missing = {col for col in columns.values() if col.lower() not in existing}
        if missing:
            raise SchedulerSchemaError(
                f"Table '{table}' is missing expected column(s) {sorted(missing)}. "
                "Your plugin version's schema may differ — see scheduler_schema.py."
            )


def per_project_columns_available(conn: sqlite3.Connection) -> bool:
    existing = {row["name"].lower() for row in conn.execute(f"PRAGMA table_info({schema.PROJECT_TABLE})").fetchall()}
    return all(col.lower() in existing for col in schema.PROJECT_OPTIONAL_COLUMNS.values())


def ts_priority(hub_priority: int) -> int:
    """The Hub sends Target Scheduler's own codes (0 low, 1 normal, 2 high);
    older Hubs sent any integer, which clamps."""
    return max(schema.PROJECT_PRIORITY_LOW, min(schema.PROJECT_PRIORITY_HIGH, int(hub_priority)))


def _insert(conn: sqlite3.Connection, table: str, values: dict) -> int:
    columns = ", ".join(f'"{column}"' for column in values)
    cursor = conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' * len(values))})", tuple(values.values()))
    return cursor.lastrowid


def _insert_project(conn: sqlite3.Connection, profile_id: str, name: str, description: str, priority: int,
                    min_altitude: float | None = None, settings: dict[str, int] | None = None) -> int:
    """A project row as the plugin's own UI writes it, with its rule weights."""
    cols = schema.PROJECT_COLUMNS
    values = {
        cols["profile_id"]: profile_id, cols["name"]: name, cols["description"]: description,
        cols["state"]: schema.PROJECT_STATE_ACTIVE, cols["priority"]: priority,
        cols["create_date"]: int(time.time()), cols["guid"]: str(uuid.uuid4()), **schema.PROJECT_DEFAULTS, **(settings or {}),
    }
    if min_altitude is not None:
        values[schema.PROJECT_OPTIONAL_COLUMNS["minimum_altitude"]] = min_altitude
    project_id = _insert(conn, schema.PROJECT_TABLE, values)
    weight_cols = schema.RULE_WEIGHT_COLUMNS
    for rule, weight in schema.DEFAULT_RULE_WEIGHTS.items():
        _insert(conn, schema.RULE_WEIGHT_TABLE, {weight_cols["name"]: rule, weight_cols["weight"]: weight, weight_cols["project_id"]: project_id})
    return project_id


def upsert_hub_project(conn: sqlite3.Connection, profile_id: str, name: str, priority: int, min_altitude: float | None,
                       existing_id: int | None = None, settings: dict[str, int | None] | None = None) -> int:
    """One Target Scheduler project per Hub project (§8.3). Matched by the id
    we recorded (so renames follow), else by name. `settings` are the Hub's
    PROJECT_SETTING_COLUMNS values; one the Hub didn't send is left as it is."""
    cols = schema.PROJECT_COLUMNS
    settings = {schema.PROJECT_SETTING_COLUMNS[key]: value for key, value in (settings or {}).items() if value is not None}
    min_alt = schema.PROJECT_OPTIONAL_COLUMNS["minimum_altitude"]
    row = None
    if existing_id is not None:
        row = conn.execute(f"SELECT {cols['id']} FROM {schema.PROJECT_TABLE} WHERE {cols['id']} = ?", (existing_id,)).fetchone()
    if row is None:
        row = conn.execute(
            f"SELECT {cols['id']} FROM {schema.PROJECT_TABLE} WHERE {cols['name']} = ? AND {cols['profile_id']} = ?", (name, profile_id)
        ).fetchone()
    if row:
        values = {cols["name"]: name, cols["priority"]: ts_priority(priority), min_alt: min_altitude or 0,
                  cols["state"]: schema.PROJECT_STATE_ACTIVE, **settings}
        conn.execute(
            f"UPDATE {schema.PROJECT_TABLE} SET {', '.join(f'{column} = ?' for column in values)} WHERE {cols['id']} = ?",
            (*values.values(), row[cols["id"]]),
        )
        return row[cols["id"]]
    return _insert_project(conn, profile_id, name, "Managed by robs (Hub project)", ts_priority(priority), min_altitude or 0, settings)


def move_target(conn: sqlite3.Connection, target_id: int, project_id: int) -> None:
    """Re-home a scheduler target (keeping its exposure plans and accepted counts)."""
    cols = schema.TARGET_COLUMNS
    conn.execute(f"UPDATE {schema.TARGET_TABLE} SET {cols['project_id']} = ? WHERE {cols['id']} = ?", (project_id, target_id))


def enabled_target_count(conn: sqlite3.Connection, project_id: int) -> int:
    cols = schema.TARGET_COLUMNS
    return conn.execute(
        f"SELECT count(*) FROM {schema.TARGET_TABLE} WHERE {cols['project_id']} = ? AND {cols['active']} = 1", (project_id,)
    ).fetchone()[0]


def set_project_state(conn: sqlite3.Connection, project_id: int, state: int) -> None:
    cols = schema.PROJECT_COLUMNS
    conn.execute(f"UPDATE {schema.PROJECT_TABLE} SET {cols['state']} = ? WHERE {cols['id']} = ?", (state, project_id))


def find_managed_project(conn: sqlite3.Connection, profile_id: str) -> int | None:
    cols = schema.PROJECT_COLUMNS
    row = conn.execute(
        f"SELECT {cols['id']} FROM {schema.PROJECT_TABLE} WHERE {cols['name']} = ? AND {cols['profile_id']} = ?",
        (schema.MANAGED_PROJECT_NAME, profile_id),
    ).fetchone()
    return row[cols["id"]] if row else None


def get_or_create_project(conn: sqlite3.Connection, profile_id: str) -> int:
    cols = schema.PROJECT_COLUMNS
    row = conn.execute(
        f"SELECT {cols['id']} FROM {schema.PROJECT_TABLE} "
        f"WHERE {cols['name']} = ? AND {cols['profile_id']} = ?",
        (schema.MANAGED_PROJECT_NAME, profile_id),
    ).fetchone()
    if row:
        return row[cols["id"]]

    return _insert_project(conn, profile_id, schema.MANAGED_PROJECT_NAME, "Managed by remote-observatory-worker",
                           schema.PROJECT_PRIORITY_NORMAL)


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

    return _insert(conn, schema.EXPOSURE_TEMPLATE_TABLE, {
        cols["profile_id"]: profile_id, cols["name"]: filter_name, cols["filter_name"]: filter_name,
        cols["default_exposure"]: default_exposure_seconds, cols["guid"]: str(uuid.uuid4()), **schema.EXPOSURE_TEMPLATE_DEFAULTS,
    })


def upsert_target(conn: sqlite3.Connection, project_id: int, name: str, ra_deg: float, dec_deg: float) -> int:
    cols = schema.TARGET_COLUMNS
    ra_hours = ra_deg / 15  # Target Scheduler stores right ascension in hours
    row = conn.execute(
        f"SELECT {cols['id']} FROM {schema.TARGET_TABLE} WHERE {cols['project_id']} = ? AND {cols['name']} = ?",
        (project_id, name),
    ).fetchone()
    if row:
        target_id = row[cols["id"]]
        conn.execute(
            f"UPDATE {schema.TARGET_TABLE} SET {cols['ra']} = ?, {cols['dec']} = ?, {cols['active']} = 1 "
            f"WHERE {cols['id']} = ?",
            (ra_hours, dec_deg, target_id),
        )
        return target_id

    return _insert(conn, schema.TARGET_TABLE, {
        cols["project_id"]: project_id, cols["name"]: name, cols["ra"]: ra_hours, cols["dec"]: dec_deg, cols["active"]: 1,
        cols["epoch_code"]: schema.TARGET_EPOCH_J2000, cols["rotation"]: 0, cols["roi"]: schema.TARGET_DEFAULT_ROI,
        cols["guid"]: str(uuid.uuid4()),
    })


def upsert_exposure_plan(conn: sqlite3.Connection, profile_id: str, target_id: int, exposure_template_id: int, desired: int,
                         exposure_seconds: float) -> int:
    """The plan carries its own exposure time, so a template shared by plans
    of different lengths (templates are matched by filter) doesn't decide it."""
    cols = schema.EXPOSURE_PLAN_COLUMNS
    row = conn.execute(
        f"SELECT {cols['id']} FROM {schema.EXPOSURE_PLAN_TABLE} "
        f"WHERE {cols['target_id']} = ? AND {cols['exposure_template_id']} = ?",
        (target_id, exposure_template_id),
    ).fetchone()
    if row:
        plan_id = row[cols["id"]]
        conn.execute(
            f"UPDATE {schema.EXPOSURE_PLAN_TABLE} SET {cols['desired']} = ?, {cols['exposure']} = ? WHERE {cols['id']} = ?",
            (desired, exposure_seconds, plan_id),
        )
        return plan_id

    return _insert(conn, schema.EXPOSURE_PLAN_TABLE, {
        cols["profile_id"]: profile_id, cols["target_id"]: target_id, cols["exposure_template_id"]: exposure_template_id,
        cols["exposure"]: exposure_seconds, cols["desired"]: desired, cols["acquired"]: 0, cols["accepted"]: 0,
        cols["enabled"]: 1, cols["guid"]: str(uuid.uuid4()),
    })


def retire_exposure_plan(conn: sqlite3.Connection, exposure_plan_id: int) -> None:
    """A plan removed in the Hub: wanted = accepted, so Target Scheduler
    treats it as done (the row and its history stay)."""
    cols = schema.EXPOSURE_PLAN_COLUMNS
    conn.execute(
        f"UPDATE {schema.EXPOSURE_PLAN_TABLE} SET {cols['desired']} = {cols['accepted']} WHERE {cols['id']} = ?",
        (exposure_plan_id,),
    )


def read_accepted_count(conn: sqlite3.Connection, exposure_plan_id: int) -> int:
    cols = schema.EXPOSURE_PLAN_COLUMNS
    row = conn.execute(
        f"SELECT {cols['accepted']} FROM {schema.EXPOSURE_PLAN_TABLE} WHERE {cols['id']} = ?",
        (exposure_plan_id,),
    ).fetchone()
    return int(row[cols["accepted"]]) if row else 0


def disable_target(conn: sqlite3.Connection, target_id: int) -> None:
    cols = schema.TARGET_COLUMNS
    conn.execute(f"UPDATE {schema.TARGET_TABLE} SET {cols['active']} = 0 WHERE {cols['id']} = ?", (target_id,))


def delete_target(conn: sqlite3.Connection, target_id: int) -> None:
    plan_cols = schema.EXPOSURE_PLAN_COLUMNS
    target_cols = schema.TARGET_COLUMNS
    conn.execute(f"DELETE FROM {schema.EXPOSURE_PLAN_TABLE} WHERE {plan_cols['target_id']} = ?", (target_id,))
    conn.execute(f"DELETE FROM {schema.TARGET_TABLE} WHERE {target_cols['id']} = ?", (target_id,))


def new_profile_id() -> str:
    """Handy for local/dev testing when a real NINA profile GUID isn't at hand."""
    return str(uuid.uuid4())
