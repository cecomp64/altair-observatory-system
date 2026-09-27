from __future__ import annotations

import json

import responses

from robs import scheduler_db, state
from robs.api_client import ObservatoryApiClient
from robs.config import TelescopeConfig
from robs.sync import sync_progress_to_api, sync_targets_into_scheduler

ACTIVE_TARGETS_RESPONSE = {
    "telescope": {"id": 1, "slug": "test-scope"},
    "targets": [
        {
            "id": 5,
            "name": "M42",
            "ra_deg": 83.8,
            "dec_deg": -5.4,
            "status": "submitted",
            "priority": 0,
            "notes": None,
            "exposure_plans": [
                {"id": 50, "filter": "Luminance", "exposure_seconds": 300, "desired_count": 20, "completed_count": 0, "remaining_count": 20}
            ],
        }
    ],
}


@responses.activate
def test_sync_targets_into_scheduler_upserts_target_and_exposure_plan(telescope_config_yaml):
    config = TelescopeConfig.load(telescope_config_yaml)
    api = ObservatoryApiClient(config.api_base_url, config.api_key)

    responses.get(
        f"{config.api_base_url}/api/v1/telescopes/{config.slug}/active_targets",
        json=ACTIVE_TARGETS_RESPONSE,
    )

    count = sync_targets_into_scheduler(config, api)

    assert count == 1

    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as conn:
        target_row = conn.execute("SELECT * FROM target").fetchone()
        assert target_row["name"] == "#5 M42"
        assert target_row["ra"] == 83.8

        plan_row = conn.execute("SELECT * FROM exposureplan").fetchone()
        assert plan_row["desired"] == 20

    state_db_path = state.state_db_path_for(config.scheduler_db_path)
    with state.open_state_db(state_db_path) as conn:
        link = state.find_target_link(conn, 5)
        assert link is not None
        plan_links = state.exposure_plan_links_for_target(conn, 5)
        assert len(plan_links) == 1


@responses.activate
def test_sync_is_idempotent_across_two_runs(telescope_config_yaml):
    config = TelescopeConfig.load(telescope_config_yaml)
    api = ObservatoryApiClient(config.api_base_url, config.api_key)

    responses.get(
        f"{config.api_base_url}/api/v1/telescopes/{config.slug}/active_targets",
        json=ACTIVE_TARGETS_RESPONSE,
    )
    responses.get(
        f"{config.api_base_url}/api/v1/telescopes/{config.slug}/active_targets",
        json=ACTIVE_TARGETS_RESPONSE,
    )

    sync_targets_into_scheduler(config, api)
    sync_targets_into_scheduler(config, api)

    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM target").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM exposureplan").fetchone()["c"] == 1


@responses.activate
def test_sync_progress_to_api_reports_accepted_counts(telescope_config_yaml):
    config = TelescopeConfig.load(telescope_config_yaml)
    api = ObservatoryApiClient(config.api_base_url, config.api_key)

    responses.get(
        f"{config.api_base_url}/api/v1/telescopes/{config.slug}/active_targets",
        json=ACTIVE_TARGETS_RESPONSE,
    )
    sync_targets_into_scheduler(config, api)

    # Simulate NINA having captured 7 frames.
    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as conn:
        conn.execute("UPDATE exposureplan SET accepted = 7")

    progress_mock = responses.patch(f"{config.api_base_url}/api/v1/targets/5/progress", json={"ok": True})

    reported = sync_progress_to_api(config, api)

    assert reported == 1
    body = json.loads(progress_mock.calls[0].request.body)
    assert body == {"exposure_plans": [{"id": 50, "completed_count": 7}]}


@responses.activate
def test_sync_follows_plan_changes_made_in_the_hub(telescope_config_yaml):
    """More frames, a new filter, and a removed plan all reach Target Scheduler."""
    import copy

    config = TelescopeConfig.load(telescope_config_yaml)
    api = ObservatoryApiClient(config.api_base_url, config.api_key)
    url = f"{config.api_base_url}/api/v1/telescopes/{config.slug}/active_targets"
    first = copy.deepcopy(ACTIVE_TARGETS_RESPONSE)
    first["targets"][0]["exposure_plans"].append(
        {"id": 51, "filter": "Ha", "exposure_seconds": 600, "desired_count": 10, "completed_count": 0, "remaining_count": 10})
    second = copy.deepcopy(ACTIVE_TARGETS_RESPONSE)
    second["targets"][0]["exposure_plans"][0]["desired_count"] = 40
    second["targets"][0]["exposure_plans"].append(
        {"id": 52, "filter": "OIII", "exposure_seconds": 600, "desired_count": 15, "completed_count": 0, "remaining_count": 15})
    responses.get(url, json=first)
    responses.get(url, json=second)

    sync_targets_into_scheduler(config, api)
    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as conn:
        ha_plan = conn.execute("SELECT p.id FROM exposureplan p JOIN exposuretemplate t ON t.id = p.exposureTemplateId "
                               "WHERE t.filtername = 'Ha'").fetchone()["id"]
        conn.execute("UPDATE exposureplan SET accepted = 3 WHERE id = ?", (ha_plan,))

    sync_targets_into_scheduler(config, api)

    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as conn:
        rows = {r["filtername"]: (r["desired"], r["accepted"]) for r in conn.execute(
            "SELECT t.filtername, p.desired, p.accepted FROM exposureplan p JOIN exposuretemplate t ON t.id = p.exposureTemplateId")}
    assert rows == {"Luminance": (40, 0), "Ha": (3, 3), "OIII": (15, 0)}
    with state.open_state_db(state.state_db_path_for(config.scheduler_db_path)) as conn:
        assert sorted(link["rails_exposure_plan_id"] for link in state.exposure_plan_links_for_target(conn, 5)) == [50, 52]
