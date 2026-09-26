"""docs/SYSTEM_ARCHITECTURE.md §8.3: per-project Target Scheduler projects,
schedule_count, nina_name, session events, the Altair data pipeline and
standalone mode."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import responses
from jsonschema import Draft202012Validator
from observatory_contracts import API_REVISION
from referencing import Registry, Resource

from robs import scheduler_db, state
from robs.api_client import ObservatoryApiClient
from robs.cleanup import cleanup_completed_projects
from robs.config import ConfigError, TelescopeConfig
from robs.end_of_night import end_of_night
from robs.hub import Hub
from robs.sync import sync_progress_to_api, sync_targets_into_scheduler

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "schemas"
REGISTRY = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in (json.loads(p.read_text()) for p in SCHEMAS.rglob("*.json")))
BASE = "https://example.test/api/v1"


def contract_errors(schema: str, data) -> list[str]:
    validator = Draft202012Validator(json.loads((SCHEMAS / schema).read_text()), registry=REGISTRY, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return [e.message for e in validator.iter_errors(data)]


def target(tid: int, project_id: int, *, name: str = "M31", desired=20, completed=5, schedule=None, min_alt=35.0):
    return {
        "id": tid, "name": name, "ra_deg": 10.68, "dec_deg": 41.27, "status": "active", "priority": 1, "notes": None,
        "nina_name": f"#{tid} {name}", "rotation_deg": None, "min_altitude_deg": min_alt,
        "project": {"id": project_id, "name": f"Project {project_id}", "priority": 7, "ts_project_name": f"#P{project_id} Project {project_id}"},
        "optical_train": {"key": "esprit100_2600mm"},
        "exposure_plans": [{"id": tid * 10, "filter": "Ha", "exposure_seconds": 300, "desired_count": desired, "completed_count": completed,
                            "remaining_count": desired - completed, **({"schedule_count": schedule} if schedule is not None else {})}],
    }


def response(*targets):
    body = {"telescope": {"id": 1, "slug": "test-scope", "name": "Test", "timezone": "America/Los_Angeles"}, "targets": list(targets)}
    assert contract_errors("worker/active_targets.response.json", body) == []
    return body


def write_config(tmp_path: Path, scheduler_db_path: Path, **extra) -> TelescopeConfig:
    lines = {"slug": "test-scope", "api_base_url": "https://example.test", "api_key": "k", "scheduler_db_path": str(scheduler_db_path),
             "subs_dir": str(tmp_path / "subs"), "nina_profile_id": "11111111-1111-1111-1111-111111111111",
             "timezone": "America/Los_Angeles", **extra}
    (tmp_path / "subs").mkdir(exist_ok=True)
    path = tmp_path / "t.yml"
    import yaml

    path.write_text(yaml.safe_dump(lines))
    return TelescopeConfig.load(path)


def rows(db: Path, sql: str):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@responses.activate
def test_one_ts_project_per_hub_project_with_priority_min_altitude_and_schedule_count(tmp_path, scheduler_db_path):
    config = write_config(tmp_path, scheduler_db_path)
    responses.get(f"{BASE}/telescopes/test-scope/active_targets",
                  json=response(target(34, 12, schedule=26), target(35, 12, name="M33"), target(40, 13, name="M42", min_alt=20)))

    assert sync_targets_into_scheduler(config, Hub.for_config(config)) == 3

    projects = {r["name"]: r for r in rows(scheduler_db_path, "SELECT * FROM project")}
    assert set(projects) == {"#P12 Project 12", "#P13 Project 13"}
    assert projects["#P12 Project 12"]["priority"] == 7
    assert projects["#P13 Project 13"]["minimumAltitude"] == 20
    targets = {r["name"]: r for r in rows(scheduler_db_path, "SELECT * FROM target")}
    assert targets["#34 M31"]["projectId"] == targets["#35 M33"]["projectId"] == projects["#P12 Project 12"]["Id"]
    plan = rows(scheduler_db_path, "SELECT desired FROM exposureplan WHERE targetId = %d" % targets["#34 M31"]["Id"])[0]
    assert plan["desired"] == 26  # schedule_count, not desired_count


@responses.activate
def test_targets_move_out_of_the_old_single_project_keeping_accepted_counts(tmp_path, scheduler_db_path):
    legacy = write_config(tmp_path, scheduler_db_path, ts_project_mode="single")
    responses.get(f"{BASE}/telescopes/test-scope/active_targets", json=response(target(34, 12)))
    sync_targets_into_scheduler(legacy, Hub.for_config(legacy))
    conn = sqlite3.connect(scheduler_db_path)
    conn.execute("UPDATE exposureplan SET accepted = 9")
    conn.commit()
    conn.close()

    per_project = write_config(tmp_path, scheduler_db_path)
    sync_targets_into_scheduler(per_project, Hub.for_config(per_project))

    targets = rows(scheduler_db_path, "SELECT t.*, p.name AS project FROM target t JOIN project p ON p.Id = t.projectId")
    assert [(t["name"], t["project"]) for t in targets] == [("#34 M31", "#P12 Project 12")]
    assert rows(scheduler_db_path, "SELECT accepted FROM exposureplan")[0]["accepted"] == 9
    old = rows(scheduler_db_path, "SELECT state FROM project WHERE name = 'Remote Observatory Queue'")[0]
    assert old["state"] == 2  # disabled once empty


@responses.activate
def test_falls_back_to_a_single_project_without_the_per_project_columns(tmp_path):
    db = tmp_path / "old_schedulerdb.sqlite"
    from conftest import CREATE_SCHEDULER_SCHEMA

    conn = sqlite3.connect(db)
    conn.executescript(CREATE_SCHEDULER_SCHEMA.replace("    minimumAltitude REAL,\n", ""))
    conn.close()
    config = write_config(tmp_path, db)
    responses.get(f"{BASE}/telescopes/test-scope/active_targets", json=response(target(34, 12)))
    sync_targets_into_scheduler(config, Hub.for_config(config))
    assert [r["name"] for r in rows(db, "SELECT name FROM project")] == ["Remote Observatory Queue"]


@responses.activate
def test_cleanup_disables_ts_projects_left_without_active_targets(tmp_path, scheduler_db_path):
    config = write_config(tmp_path, scheduler_db_path)
    responses.get(f"{BASE}/telescopes/test-scope/active_targets", json=response(target(34, 12), target(40, 13, name="M42")))
    sync_targets_into_scheduler(config, Hub.for_config(config))
    responses.replace(responses.GET, f"{BASE}/telescopes/test-scope/active_targets", json=response(target(34, 12)))

    assert cleanup_completed_projects(config, Hub.for_config(config)) == 1
    states = {r["name"]: r["state"] for r in rows(scheduler_db_path, "SELECT name, state FROM project")}
    assert states == {"#P12 Project 12": 1, "#P13 Project 13": 2}


@responses.activate
def test_end_of_night_posts_session_end_and_uploads_nothing(tmp_path, scheduler_db_path):
    config = write_config(tmp_path, scheduler_db_path)
    responses.get(f"{BASE}/telescopes/test-scope/active_targets", json=response(target(34, 12)))
    hub = Hub.for_config(config)
    sync_targets_into_scheduler(config, hub)
    progress = responses.patch(f"{BASE}/targets/34/progress", json={"ok": True, "target": {"id": 34, "status": "active", "percent_complete": 25}})
    session = responses.post(f"{BASE}/telescopes/test-scope/sessions", json={"ok": True}, status=201)
    (config.subs_dir / "#34 M31").mkdir()
    (config.subs_dir / "#34 M31" / "L_0001.fits").write_bytes(b"data")

    result = end_of_night(config, hub)

    assert progress.call_count == 1
    body = json.loads(session.calls[0].request.body)
    assert contract_errors("worker/session_event.request.json", body) == []
    assert body["event"] == "session_end" and body["target_ids"] == [34]
    assert not any("/files" in c.request.url for c in responses.calls)
    assert not config.marker_dir.exists()


def test_standalone_reads_targets_file_logs_progress_and_writes_the_altair_marker(tmp_path, scheduler_db_path):
    targets_file = tmp_path / "targets.json"
    targets_file.write_text(json.dumps(response(target(34, 12))))
    config = write_config(tmp_path, scheduler_db_path, hub={"enabled": False}, targets_file=str(targets_file),
                          api_base_url=None, api_key=None)
    hub = Hub.for_config(config)
    assert hub.api is None

    assert sync_targets_into_scheduler(config, hub) == 1
    assert sync_progress_to_api(config, hub) == 1
    result = end_of_night(config, hub)

    log = [json.loads(line) for line in config.event_log_path.read_text().splitlines()]
    # Progress from sync-progress and from end-of-night's final sync; the session
    # end goes to Altair as the marker, not the log.
    assert [e["kind"] for e in log] == ["progress", "progress"]
    markers = list(config.marker_dir.glob("session-end-*.json"))
    assert len(markers) == 1 and "marker" in result["signal"]
    marker = json.loads(markers[0].read_text())
    assert marker["telescope"] == "test-scope" and marker["target_ids"] == [34] and marker["night"]


def test_standalone_needs_a_targets_file(tmp_path, scheduler_db_path):
    with pytest.raises(ConfigError, match="targets_file"):
        write_config(tmp_path, scheduler_db_path, hub={"enabled": False})


@responses.activate
def test_session_and_heartbeat_requests_match_the_contract():
    api = ObservatoryApiClient("https://example.test", "k")
    responses.post(f"{BASE}/telescopes/test-scope/sessions", json={"ok": True}, status=201)
    responses.post(f"{BASE}/heartbeat", json={"ok": True, "api_revision": 1})
    api.post_session_event("test-scope", "roof_open", "2026-09-25T03:00:00Z", "2026-09-24", [34])
    api.heartbeat({"telescope": "test-scope"})
    session, heartbeat = (json.loads(c.request.body) for c in responses.calls)
    assert contract_errors("worker/session_event.request.json", session) == []
    assert contract_errors("shared/heartbeat.request.json", heartbeat) == []
    assert heartbeat["agent"] == "robs"


def test_night_rolls_over_at_local_noon(tmp_path, scheduler_db_path):
    from datetime import datetime, timezone

    hub = Hub(write_config(tmp_path, scheduler_db_path), None)
    assert hub.night_for(datetime(2026, 9, 25, 12, 41, tzinfo=timezone.utc)) == "2026-09-24"  # 05:41 PDT
    assert hub.night_for(datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)) == "2026-09-25"  # 13:00 PDT


@responses.activate
@pytest.mark.parametrize("hub_revision, ok", [(API_REVISION, True), (API_REVISION + 1, True), (API_REVISION - 1, False)])
def test_check_config_compares_the_hubs_api_revision(tmp_path, scheduler_db_path, hub_revision, ok):
    from click.testing import CliRunner

    from robs.cli import main

    write_config(tmp_path, scheduler_db_path)
    responses.get(f"{BASE}/telescopes/test-scope/active_targets", json=response())
    responses.post(f"{BASE}/heartbeat", json={"ok": True, "api_revision": hub_revision})

    result = CliRunner().invoke(main, ["check-config", "--config", str(tmp_path / "t.yml")])

    assert ("[ok] Hub API revision compatible" in result.output) is ok, result.output
    assert ("[FAIL] Hub API revision compatible" in result.output) is not ok
