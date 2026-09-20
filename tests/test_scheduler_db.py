from __future__ import annotations

import sqlite3

import pytest

from robs import scheduler_db
from robs.scheduler_db import SchedulerSchemaError

PROFILE_ID = "11111111-1111-1111-1111-111111111111"


def test_ensure_schema_compatible_passes_on_matching_schema(scheduler_db_path):
    with scheduler_db.open_scheduler_db(scheduler_db_path) as conn:
        scheduler_db.ensure_schema_compatible(conn)  # should not raise


def test_ensure_schema_compatible_fails_on_missing_table(tmp_path):
    db_path = tmp_path / "empty.sqlite"
    sqlite3.connect(str(db_path)).close()

    with scheduler_db.open_scheduler_db(db_path) as conn:
        with pytest.raises(SchedulerSchemaError, match="project"):
            scheduler_db.ensure_schema_compatible(conn)


def test_missing_db_file_raises_clear_error(tmp_path):
    with pytest.raises(SchedulerSchemaError):
        with scheduler_db.open_scheduler_db(tmp_path / "nope.sqlite"):
            pass


def test_get_or_create_project_is_idempotent(scheduler_db_path):
    with scheduler_db.open_scheduler_db(scheduler_db_path) as conn:
        first = scheduler_db.get_or_create_project(conn, PROFILE_ID)
        second = scheduler_db.get_or_create_project(conn, PROFILE_ID)

    assert first == second


def test_upsert_target_creates_then_updates(scheduler_db_path):
    with scheduler_db.open_scheduler_db(scheduler_db_path) as conn:
        project_id = scheduler_db.get_or_create_project(conn, PROFILE_ID)

        target_id_1 = scheduler_db.upsert_target(conn, project_id, "#5 M42", 10.0, 20.0)
        target_id_2 = scheduler_db.upsert_target(conn, project_id, "#5 M42", 11.0, 21.0)

        assert target_id_1 == target_id_2

        row = conn.execute("SELECT ra, dec FROM target WHERE Id = ?", (target_id_1,)).fetchone()
        assert (row["ra"], row["dec"]) == (11.0, 21.0)


def test_upsert_exposure_plan_and_read_accepted_count(scheduler_db_path):
    with scheduler_db.open_scheduler_db(scheduler_db_path) as conn:
        project_id = scheduler_db.get_or_create_project(conn, PROFILE_ID)
        target_id = scheduler_db.upsert_target(conn, project_id, "#5 M42", 10.0, 20.0)
        template_id = scheduler_db.get_or_create_exposure_template(conn, PROFILE_ID, "Luminance", 300)

        plan_id = scheduler_db.upsert_exposure_plan(conn, target_id, template_id, desired=20)
        assert scheduler_db.read_accepted_count(conn, plan_id) == 0

        conn.execute("UPDATE exposureplan SET accepted = 7 WHERE Id = ?", (plan_id,))
        assert scheduler_db.read_accepted_count(conn, plan_id) == 7

        # Re-upserting with a new desired count keeps the same row / accepted count.
        plan_id_again = scheduler_db.upsert_exposure_plan(conn, target_id, template_id, desired=25)
        assert plan_id_again == plan_id
        assert scheduler_db.read_accepted_count(conn, plan_id) == 7


def test_disable_target(scheduler_db_path):
    with scheduler_db.open_scheduler_db(scheduler_db_path) as conn:
        project_id = scheduler_db.get_or_create_project(conn, PROFILE_ID)
        target_id = scheduler_db.upsert_target(conn, project_id, "#5 M42", 10.0, 20.0)

        scheduler_db.disable_target(conn, target_id)

        row = conn.execute("SELECT enabled FROM target WHERE Id = ?", (target_id,)).fetchone()
        assert row["enabled"] == 0
