from __future__ import annotations

import sqlite3

import pytest

from conftest import CREATE_SCHEDULER_SCHEMA
from robs import scheduler_db, scheduler_schema
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
        assert (row["ra"], row["dec"]) == (pytest.approx(11.0 / 15), 21.0)  # right ascension in hours


def test_upsert_exposure_plan_and_read_accepted_count(scheduler_db_path):
    with scheduler_db.open_scheduler_db(scheduler_db_path) as conn:
        project_id = scheduler_db.get_or_create_project(conn, PROFILE_ID)
        target_id = scheduler_db.upsert_target(conn, project_id, "#5 M42", 10.0, 20.0)
        template_id = scheduler_db.get_or_create_exposure_template(conn, PROFILE_ID, "Luminance", 300)

        plan_id = scheduler_db.upsert_exposure_plan(conn, PROFILE_ID, target_id, template_id, desired=20, exposure_seconds=300)
        assert scheduler_db.read_accepted_count(conn, plan_id) == 0

        conn.execute("UPDATE exposureplan SET accepted = 7 WHERE Id = ?", (plan_id,))
        assert scheduler_db.read_accepted_count(conn, plan_id) == 7

        # Re-upserting with a new desired count keeps the same row / accepted count.
        plan_id_again = scheduler_db.upsert_exposure_plan(conn, PROFILE_ID, target_id, template_id, desired=25, exposure_seconds=300)
        assert plan_id_again == plan_id
        assert scheduler_db.read_accepted_count(conn, plan_id) == 7


def test_disable_target(scheduler_db_path):
    with scheduler_db.open_scheduler_db(scheduler_db_path) as conn:
        project_id = scheduler_db.get_or_create_project(conn, PROFILE_ID)
        target_id = scheduler_db.upsert_target(conn, project_id, "#5 M42", 10.0, 20.0)

        scheduler_db.disable_target(conn, target_id)

        row = conn.execute("SELECT active FROM target WHERE Id = ?", (target_id,)).fetchone()
        assert row["active"] == 0


def test_rows_are_written_the_way_target_scheduler_writes_them(scheduler_db_path):
    with scheduler_db.open_scheduler_db(scheduler_db_path) as conn:
        project_id = scheduler_db.upsert_hub_project(conn, PROFILE_ID, "#P12 Andromeda deep", priority=1, min_altitude=30)
        target_id = scheduler_db.upsert_target(conn, project_id, "#34 M31", 10.68471, 41.26875)
        template_id = scheduler_db.get_or_create_exposure_template(conn, PROFILE_ID, "Ha", 300)
        plan_id = scheduler_db.upsert_exposure_plan(conn, PROFILE_ID, target_id, template_id, desired=126, exposure_seconds=300)

        project = conn.execute("SELECT * FROM project WHERE Id = ?", (project_id,)).fetchone()
        assert (project["state"], project["priority"], project["minimumaltitude"]) == (1, 1, 30)
        assert project["guid"] and project["createdate"]
        assert None not in (project["minimumtime"], project["usecustomhorizon"], project["horizonoffset"], project["meridianwindow"],
                            project["filterswitchfrequency"], project["ditherevery"], project["enablegrader"])
        weights = conn.execute("SELECT name FROM ruleweight WHERE projectid = ?", (project_id,)).fetchall()
        assert {w["name"] for w in weights} == set(scheduler_schema.DEFAULT_RULE_WEIGHTS)

        target = conn.execute("SELECT * FROM target WHERE Id = ?", (target_id,)).fetchone()
        assert target["ra"] == pytest.approx(0.712314)  # hours
        assert (target["dec"], target["active"], target["epochcode"], target["roi"]) == (41.26875, 1, 2, 100)

        template = conn.execute("SELECT * FROM exposuretemplate WHERE Id = ?", (template_id,)).fetchone()
        assert (template["name"], template["filtername"], template["defaultexposure"], template["bin"]) == ("Ha", "Ha", 300, 1)

        plan = conn.execute("SELECT * FROM exposureplan WHERE Id = ?", (plan_id,)).fetchone()
        assert (plan["profileId"], plan["exposure"], plan["desired"], plan["acquired"], plan["accepted"], plan["enabled"]) == (
            PROFILE_ID, 300, 126, 0, 0, 1)


def test_schema_check_ignores_column_name_case(tmp_path):
    db_path = tmp_path / "upper.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(CREATE_SCHEDULER_SCHEMA.replace("`createdate`", "`createDate`").replace("`minimumaltitude`", "`minimumAltitude`"))
    conn.close()

    with scheduler_db.open_scheduler_db(db_path) as conn:
        scheduler_db.ensure_schema_compatible(conn)
        assert scheduler_db.per_project_columns_available(conn)


@pytest.mark.parametrize("hub_priority, expected", [(-3, 0), (0, 0), (1, 1), (2, 2), (7, 2)])
def test_hub_priority_clamps_to_target_scheduler_priorities(hub_priority, expected):
    assert scheduler_db.ts_priority(hub_priority) == expected


def test_backup_copies_the_database_and_keeps_only_the_newest(scheduler_db_path, monkeypatch):
    monkeypatch.setattr(scheduler_db, "BACKUPS_KEPT", 2)
    with scheduler_db.open_scheduler_db(scheduler_db_path) as conn:
        scheduler_db.get_or_create_project(conn, PROFILE_ID)

    backups = [scheduler_db.backup_scheduler_db(scheduler_db_path) for _ in range(3)]

    folder = scheduler_db_path.parent / "robs_backups"
    assert sorted(folder.iterdir()) == backups[1:]
    with scheduler_db.open_scheduler_db(backups[-1]) as conn:
        assert [r["name"] for r in conn.execute("SELECT name FROM project")] == ["Remote Observatory Queue"]


def test_backup_failure_raises_and_leaves_no_partial_copy(scheduler_db_path):
    (scheduler_db_path.parent / "robs_backups").write_text("a file where the folder should be")

    with pytest.raises(scheduler_db.SchedulerBackupError, match="Couldn't back up"):
        scheduler_db.backup_scheduler_db(scheduler_db_path)
