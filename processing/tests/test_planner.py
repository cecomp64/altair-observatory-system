"""Planning a night (SPEC §6.4): calibration jobs, light groups, matching,
issues with requirements, provisional stacks, the project reference, and
idempotent plan hashes."""
from __future__ import annotations

import json

import pytest

from altair.catalog.db import Catalog
from altair.planner.plan import Planner

from helpers import add_frame, add_master, pipeline_config


@pytest.fixture
def env(tmp_path):
    config = pipeline_config(tmp_path, triggers={"min_lights_per_stack": 3})
    catalog = Catalog(config.catalog_path)
    yield config, catalog, Planner(catalog, config)
    catalog.close()


def lights(catalog, n=5, **kw):
    return [add_frame(catalog, "light", **kw) for _ in range(n)]


def jobs(catalog, kind=None):
    rows = catalog.query("SELECT * FROM jobs" + (" WHERE kind = ?" if kind else "") + " ORDER BY id", (kind,) if kind else ())
    return [{**dict(r), "plan": json.loads(r["plan_json"])} for r in rows]


def test_a_fully_calibrated_night_gets_a_reference_then_a_final_stack(env):
    config, catalog, planner = env
    lights(catalog)
    add_master(catalog, "DARK")
    _, flat_sha = add_master(catalog, "FLAT", filter_="Ha", rotator_pos=31260, night="2026-09-24")
    plan = planner.plan_night("esprit", "2026-09-24")

    assert [g.calibrated for g in plan.groups] == [True]
    (ref,) = jobs(catalog, "PROJECT_REFERENCE")
    (stack,) = jobs(catalog, "NIGHT_STACK")
    assert stack["plan"]["stack_kind"] == "final" and stack["plan"]["reference"] == {"job": ref["plan_hash"]}
    assert json.loads(stack["depends_on_json"]) == [ref["id"]]
    assert stack["plan"]["groups"][0]["flat"] == {"sha256": flat_sha}
    project = catalog.one("SELECT * FROM projects")
    assert (project["hub_target_id"], project["rig"], project["path"]) == (34, "esprit", "projects/esprit/T34_untitled")
    assert not catalog.query("SELECT * FROM issues WHERE status = 'open'")


def test_planning_twice_is_idempotent(env):
    config, catalog, planner = env
    lights(catalog)
    add_master(catalog, "DARK")
    add_master(catalog, "FLAT", filter_="Ha", rotator_pos=31250)
    planner.plan_night("esprit", "2026-09-24")
    planner.plan_night("esprit", "2026-09-24")
    assert len(jobs(catalog)) == 2


def test_a_missing_flat_raises_an_issue_holds_the_lights_and_makes_a_provisional_stack(env):
    config, catalog, planner = env
    ids = lights(catalog)
    add_master(catalog, "DARK")
    add_master(catalog, "FLAT", filter_="Ha", rotator_pos=18400, night="2026-09-20")   # wrong rotator position
    plan = planner.plan_night("esprit", "2026-09-24")

    issue = catalog.one("SELECT * FROM issues WHERE kind = 'FLAT_MISSING'")
    assert issue["status"] == "open" and "rotator Δ 12850" in issue["message"] and "5 lights" in issue["message"]
    requirement = json.loads(issue["requirement_json"])
    assert requirement["kind"] == "FLAT" and requirement["need"]["rotator_pos"] == 31250.0
    assert json.loads(issue["scope_json"])["frame_ids"] == [i for i, _ in ids]
    assert {r["status"] for r in catalog.query("SELECT status FROM frames")} == {"held"}
    assert [s["plan"]["stack_kind"] for s in plan.stacks] == ["provisional_noflat"]
    assert not jobs(catalog, "PROJECT_REFERENCE")   # only final stacks make a reference


def test_a_matching_flat_arriving_later_resolves_the_issue_and_releases_the_lights(env):
    config, catalog, planner = env
    lights(catalog)
    add_master(catalog, "DARK")
    planner.plan_night("esprit", "2026-09-24")
    assert catalog.one("SELECT status FROM issues WHERE kind = 'FLAT_MISSING'")["status"] == "open"
    add_master(catalog, "FLAT", filter_="Ha", rotator_pos=31250, night="2026-09-26")
    planner.plan_night("esprit", "2026-09-24")
    assert catalog.one("SELECT status, resolution FROM issues WHERE kind = 'FLAT_MISSING'")["status"] == "resolved"
    assert {r["status"] for r in catalog.query("SELECT status FROM frames")} == {"valid"}
    assert {j["plan"]["stack_kind"] for j in jobs(catalog, "NIGHT_STACK")} == {"final", "provisional_noflat"}


def test_the_nights_calibration_subs_become_master_jobs_that_the_lights_use(env):
    config, catalog, planner = env
    lights(catalog)
    for _ in range(3):
        add_frame(catalog, "dark", sensor_temp=-10.0)
        add_frame(catalog, "darkflat", exposure=2.0)
        add_frame(catalog, "flat", exposure=2.0, rotator_pos=31255.0)
    plan = planner.plan_night("esprit", "2026-09-24")
    calib = jobs(catalog, "CALIB_MASTER")
    kinds = [j["plan"]["master"]["kind"] for j in calib]
    assert kinds == ["DARKFLAT", "DARK", "FLAT"]
    flat_job = calib[2]
    assert flat_job["plan"]["calibrate_with"] == {"darkflat": {"job": calib[0]["plan_hash"]}}
    (stack,) = jobs(catalog, "NIGHT_STACK")
    group = stack["plan"]["groups"][0]
    assert group["dark"] == {"job": calib[1]["plan_hash"]} and group["flat"] == {"job": calib[2]["plan_hash"]}
    assert set(json.loads(stack["depends_on_json"])) >= {calib[1]["id"], calib[2]["id"]}
    assert plan.groups[0].calibrated


def test_flats_without_their_darkflat_or_bias_raise_an_issue(env):
    config, catalog, planner = env
    for _ in range(3):
        add_frame(catalog, "flat", exposure=2.0)
    planner.plan_night("esprit", "2026-09-24")
    assert catalog.one("SELECT kind FROM issues")["kind"] == "DARKFLAT_MISSING"
    assert not jobs(catalog, "CALIB_MASTER") or all(j["plan"]["master"]["kind"] != "FLAT" for j in jobs(catalog, "CALIB_MASTER"))


def test_lights_at_two_rotator_positions_need_two_flats_but_make_one_stack(env):
    config, catalog, planner = env
    lights(catalog, 3, rotator_pos=31250.0)
    lights(catalog, 3, rotator_pos=50000.0)
    add_master(catalog, "DARK")
    add_master(catalog, "FLAT", filter_="Ha", rotator_pos=31250)
    plan = planner.plan_night("esprit", "2026-09-24")
    assert len(plan.groups) == 2 and [g.calibrated for g in plan.groups].count(True) == 1
    assert catalog.one("SELECT count(*) AS n FROM issues WHERE kind = 'FLAT_MISSING' AND status = 'open'")["n"] == 1
    add_master(catalog, "FLAT", filter_="Ha", rotator_pos=50010)
    plan = planner.plan_night("esprit", "2026-09-24")
    finals = [s for s in plan.stacks if s["plan"]["stack_kind"] == "final"]
    assert len(finals) == 1 and len(finals[0]["plan"]["groups"]) == 2


def test_missing_dark_blocks_without_a_provisional(env):
    config, catalog, planner = env
    lights(catalog)
    add_master(catalog, "FLAT", filter_="Ha", rotator_pos=31250)
    plan = planner.plan_night("esprit", "2026-09-24")
    assert plan.stacks == [] and catalog.one("SELECT kind FROM issues")["kind"] == "DARK_MISSING"


def test_unresolved_and_invalid_lights_are_left_out(env):
    config, catalog, planner = env
    config.hub.enabled = True
    lights(catalog, 2)
    lights(catalog, 3, hub_target_id=None, target="Mystery", status="held")
    add_frame(catalog, "light", status="invalid")
    add_master(catalog, "DARK")
    add_master(catalog, "FLAT", filter_="Ha", rotator_pos=31250)
    plan = planner.plan_night("esprit", "2026-09-24")
    assert [len(g.frames) for g in plan.groups] == [2]
    assert plan.stacks == [] and "min_lights_per_stack" in plan.skipped[0]


def test_a_focal_length_jump_proposes_an_equipment_event(env):
    config, catalog, planner = env
    lights(catalog, 3, night="2026-09-23", focal_length=550.0)
    lights(catalog, 3, focal_length=420.0)
    planner.plan_night("esprit", "2026-09-24")
    issue = catalog.one("SELECT severity, message FROM issues WHERE kind = 'EQUIPMENT_CHANGE_SUSPECTED'")
    assert issue["severity"] == "warning" and "550 mm" in issue["message"] and "420 mm" in issue["message"]


def test_plan_requests_from_commands_replan_the_right_nights(env):
    config, catalog, planner = env
    lights(catalog)
    add_master(catalog, "DARK")
    catalog.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES ('night_ready', ?, 'session_end', 'now')",
                    (json.dumps({"rig": "esprit", "night": "2026-09-24"}),))
    catalog.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES ('rerun', ?, 'hub', 'now')",
                    (json.dumps({"target_id": 34, "night": "2026-09-24"}),))
    results = planner.process_requests()
    assert [r["planned"] for r in results] == [[("esprit", "2026-09-24")], [("esprit", "2026-09-24")]]
    assert not catalog.query("SELECT 1 FROM plan_requests WHERE done_at IS NULL")


def test_altair_plan_is_a_dry_run(env, tmp_path):
    import yaml
    from click.testing import CliRunner

    from altair.cli import main

    config, catalog, _ = env
    lights(catalog)
    add_master(catalog, "DARK")
    path = tmp_path / "altair.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    out = CliRunner().invoke(main, ["--config", str(path), "plan", "--night", "2026-09-24"], catch_exceptions=False).output
    assert "5 Ha light(s)" in out and "dark ok" in out and "FLAT_MISSING" in out
    assert not catalog.query("SELECT 1 FROM jobs") and not catalog.query("SELECT 1 FROM issues")
