"""Manual issue controls (SPEC §10.4): waive, resolve with a given flat, the flats plan."""
from __future__ import annotations

import json

import pytest

from altair.catalog.db import Catalog
from altair.executor.executor import Executor
from altair.issue_actions import ActionError, flats_plan, resolve_with_flat, waive
from altair.planner.plan import Planner

from conftest import write_fits
from helpers import add_frame, fake_pixinsight, pipeline_config


@pytest.fixture
def env(tmp_path):
    config = pipeline_config(tmp_path, pixinsight=fake_pixinsight(tmp_path))
    catalog = Catalog(config.catalog_path)
    nas = config.storage.nas.root
    for i in range(6):
        add_frame(catalog, night="2026-09-24", date_obs=f"2026-09-25T06:{i:02d}:00Z", nas_root=nas, width=60, height=40)
    for i in range(3):
        add_frame(catalog, "dark", night="2026-09-24", date_obs=f"2026-09-25T12:{i:02d}:00Z", nas_root=nas, width=60, height=40)
    Planner(catalog, config).plan_night("esprit", "2026-09-24")
    Executor(catalog, config).run_all()
    issue = catalog.one("SELECT * FROM issues WHERE kind = 'FLAT_MISSING'")
    return config, catalog, issue, tmp_path


def test_flats_plan_lists_what_to_shoot(env):
    config, catalog, issue, _ = env
    md = flats_plan(catalog)
    assert "| esprit | Ha | 31250.0 | 1x1 | 550.0 | 2026-09-24 |" in md and f"#{issue['id']}" in md
    assert flats_plan(catalog, fmt="csv").splitlines()[0] == "rig,filter,rotator_pos,binning,focal_length_mm,nights,issues"


def test_waive_excludes_the_night(env):
    config, catalog, issue, _ = env
    result = waive(catalog, config, issue["id"], "no flats for this one")
    assert catalog.one("SELECT status, resolution FROM issues WHERE id = ?", (issue["id"],))["status"] == "waived"
    project_id = json.loads(issue["scope_json"])["project_id"]
    assert result["excluded"] == [(project_id, "2026-09-24", "Ha")]
    assert catalog.one("SELECT decision FROM night_decisions")["decision"] == "exclude"
    with pytest.raises(ActionError, match="waived"):
        waive(catalog, config, issue["id"], "again")


def test_resolve_with_a_flat_checks_it_and_force_records_an_override(env):
    config, catalog, issue, tmp_path = env
    wrong = write_fits(tmp_path / "wrong_flat.fits", imagetyp="FLAT", object_="", exposure=2.5, date_obs="2026-09-24T04:00:00",
                       rotator=18400, seed=5)
    with pytest.raises(ActionError, match="rotator Δ 12850"):
        resolve_with_flat(catalog, config, issue["id"], wrong)
    result = resolve_with_flat(catalog, config, issue["id"], wrong, force=True)
    assert result["forced"]
    Planner(catalog, config).process_requests()
    Executor(catalog, config).run_all()
    final = catalog.one("SELECT * FROM night_masters WHERE kind = 'final'")
    assert final is not None
    assert json.loads(final["calib_json"])["groups"][0]["flat_evidence"]["flat_override"] is True
    assert catalog.one("SELECT status FROM issues WHERE id = ?", (issue["id"],))["status"] == "resolved"
