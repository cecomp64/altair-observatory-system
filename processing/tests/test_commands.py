from altair import frames as frame_ops

from conftest import TRAIN
from helpers import index_night, make_sync


def test_night_ready_closes_the_night_and_reports_it(catalog, config, tmp_path, fake_hub):
    index_night(catalog, config, tmp_path, fake_hub, lights=2)
    fake_hub.queue_command(1, "night_ready", {"optical_train": TRAIN, "night": "2026-09-24", "at": "2026-09-25T12:41:00Z", "closed_by": "session_end"})
    sync = make_sync(catalog, config, fake_hub)
    sync.commands.poll()
    sync.drainer.drain()

    night = catalog.one("SELECT * FROM collections WHERE rig = 'esprit' AND night = '2026-09-24'")
    assert (night["state"], night["closed_by"]) == ("closed", "session_end")
    assert fake_hub.acks[1]["state"] == "succeeded"
    assert fake_hub.nights[(TRAIN, "2026-09-24")] == {"state": "closed", "closed_by": "session_end", "session_end_at": "2026-09-25T12:41:00Z",
                                                     "lights_count": 2, "calibration_count": 1, "light_seconds": 600.0}
    assert catalog.one("SELECT kind FROM plan_requests WHERE kind = 'night_ready'")
    assert fake_hub.contract_violations == []


def test_commands_run_once_even_if_delivered_twice(catalog, config, fake_hub):
    sync = make_sync(catalog, config, fake_hub)
    fake_hub.queue_command(5, "rerun", {"target_id": 34, "night": "2026-09-24", "filter": "Ha"})
    sync.commands.poll()
    fake_hub.commands[0]["_state"] = "pending"  # the Hub redelivers (say our ack was lost)
    sync.commands.poll()
    assert catalog.one("SELECT count(*) AS n FROM plan_requests WHERE kind = 'rerun'")["n"] == 1
    assert fake_hub.acks[5] == {"state": "succeeded", "result": {"queued": True, "plan_request_id": 1}}


def test_lights_pointing_at_a_target_link_by_coordinates_without_a_token(catalog, config, tmp_path, fake_hub):
    report = index_night(catalog, config, tmp_path, fake_hub, lights=3, object_="Andromeda?")
    assert report.linked == 3
    assert {r["assignment_source"] for r in catalog.query("SELECT assignment_source FROM frames WHERE image_type = 'light'")} == {"coords"}


def test_assign_frames_command_releases_held_lights_and_resolves_the_issue(catalog, config, tmp_path, fake_hub):
    from conftest import write_fits
    from altair.hub.config_sync import HubConfig
    from altair.index.indexer import index

    for i in range(2):
        write_fits(tmp_path / "far" / f"L{i}.fits", object_="Mystery", ra=150.0, dec=10.0, seed=i)
    report = index(catalog, config, tmp_path / "far", rig="esprit", hub_config=HubConfig.from_payload(fake_hub.config))
    assert report.unlinked == 2
    assert {r["status"] for r in catalog.query("SELECT status FROM frames")} == {"held"}
    issue = catalog.one("SELECT * FROM issues WHERE kind = 'PROJECT_UNRESOLVED'")
    assert issue["status"] == "open"

    fake_hub.queue_command(9, "assign_frames", {"target_id": 35, "selector": {"optical_train": TRAIN, "night": "2026-09-24", "object": "Mystery"}})
    sync = make_sync(catalog, config, fake_hub)
    sync.commands.poll()
    sync.drainer.drain()

    rows = catalog.query("SELECT status, hub_target_id, assignment_source FROM frames")
    assert {(r["status"], r["hub_target_id"], r["assignment_source"]) for r in rows} == {("valid", 35, "manual")}
    assert catalog.one("SELECT status FROM issues WHERE kind = 'PROJECT_UNRESOLVED'")["status"] == "resolved"
    assert fake_hub.issues[issue["fingerprint"]]["status"] == "resolved"
    assert fake_hub.acks[9]["result"] == {"frames_assigned": 2}
    replan = catalog.one("SELECT payload_json FROM plan_requests WHERE kind = 'replan'")
    assert "35" in replan["payload_json"]


def test_waive_equipment_event_and_set_mode(catalog, config, fake_hub):
    sync = make_sync(catalog, config, fake_hub)
    sync.pull_config()
    with catalog.transaction() as tx:
        from altair.issues import raise_issue

        raise_issue(tx, config, kind="FLAT_MISSING", severity="blocking", fingerprint="FLAT_MISSING:x", message="flats", scope={"rig": "esprit"})
    catalog.execute("INSERT INTO projects(target, telescope, camera, rig, hub_target_id) VALUES ('M31', 'esprit100', 'asi2600mm', 'esprit', 34)")
    fake_hub.queue_command(1, "issue_waive", {"fingerprint": "FLAT_MISSING:x", "note": "sky flats"})
    fake_hub.queue_command(2, "equipment_event", {"equipment_event_id": 7})
    fake_hub.queue_command(3, "set_mode", {"target_id": 34, "mode": "frame_reintegration"})
    fake_hub.queue_command(4, "issue_waive", {"fingerprint": "NOPE", "note": "x"})
    sync.commands.poll()

    assert catalog.one("SELECT status, resolution FROM issues WHERE fingerprint = 'FLAT_MISSING:x'")["resolution"] == "waived:sky flats"
    assert catalog.one("SELECT kind, rig FROM equipment_events")["kind"] == "sensor_cleaned"
    assert catalog.one("SELECT multi_night_mode FROM projects")["multi_night_mode"] == "frame_reintegration"
    assert [fake_hub.acks[i]["state"] for i in (1, 2, 3, 4)] == ["succeeded", "succeeded", "succeeded", "failed"]


def test_unknown_optical_train_fails_the_command(catalog, config, fake_hub):
    fake_hub.queue_command(1, "night_ready", {"optical_train": "nope", "night": "2026-09-24", "at": "2026-09-25T12:41:00Z", "closed_by": "session_end"})
    make_sync(catalog, config, fake_hub).commands.poll()
    assert fake_hub.acks[1]["state"] == "failed"
    assert "nope" in fake_hub.acks[1]["result"]["error"]
