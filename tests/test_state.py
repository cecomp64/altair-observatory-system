from __future__ import annotations

from robs import state


def test_link_and_find_target(tmp_path):
    db_path = tmp_path / "state.sqlite"

    with state.open_state_db(db_path) as conn:
        state.link_target(conn, rails_target_id=1, scheduler_target_id=100, scheduler_project_id=9)

    with state.open_state_db(db_path) as conn:
        row = state.find_target_link(conn, 1)
        assert row["scheduler_target_id"] == 100
        assert row["scheduler_project_id"] == 9


def test_link_target_upserts_on_conflict(tmp_path):
    db_path = tmp_path / "state.sqlite"

    with state.open_state_db(db_path) as conn:
        state.link_target(conn, 1, 100, 9)
        state.link_target(conn, 1, 200, 9)

    with state.open_state_db(db_path) as conn:
        row = state.find_target_link(conn, 1)
        assert row["scheduler_target_id"] == 200


def test_exposure_plan_links_scoped_to_target(tmp_path):
    db_path = tmp_path / "state.sqlite"

    with state.open_state_db(db_path) as conn:
        state.link_target(conn, 1, 100, 9)
        state.link_target(conn, 2, 101, 9)
        state.link_exposure_plan(conn, rails_exposure_plan_id=10, rails_target_id=1, scheduler_exposure_plan_id=1000, filter="L")
        state.link_exposure_plan(conn, rails_exposure_plan_id=11, rails_target_id=2, scheduler_exposure_plan_id=1001, filter="Ha")

    with state.open_state_db(db_path) as conn:
        links = state.exposure_plan_links_for_target(conn, 1)
        assert len(links) == 1
        assert links[0]["scheduler_exposure_plan_id"] == 1000


def test_remove_target_link_cascades_exposure_plans(tmp_path):
    db_path = tmp_path / "state.sqlite"

    with state.open_state_db(db_path) as conn:
        state.link_target(conn, 1, 100, 9)
        state.link_exposure_plan(conn, 10, 1, 1000, "L")
        state.remove_target_link(conn, 1)

    with state.open_state_db(db_path) as conn:
        assert state.find_target_link(conn, 1) is None
        assert state.exposure_plan_links_for_target(conn, 1) == []
