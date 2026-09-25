from __future__ import annotations

import responses

from robs import scheduler_db, state
from robs.api_client import ObservatoryApiClient
from robs.cleanup import cleanup_completed_projects
from robs.config import TelescopeConfig
from robs.sync import sync_targets_into_scheduler

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

EMPTY_ACTIVE_TARGETS_RESPONSE = {"telescope": {"id": 1, "slug": "test-scope"}, "targets": []}


@responses.activate
def test_cleanup_disables_targets_no_longer_active(telescope_config_yaml):
    config = TelescopeConfig.load(telescope_config_yaml)
    api = ObservatoryApiClient(config.api_base_url, config.api_key)

    responses.get(
        f"{config.api_base_url}/api/v1/telescopes/{config.slug}/active_targets",
        json=ACTIVE_TARGETS_RESPONSE,
    )
    sync_targets_into_scheduler(config, api)

    # Rails now considers target 5 completed, so it drops out of active_targets.
    responses.get(
        f"{config.api_base_url}/api/v1/telescopes/{config.slug}/active_targets",
        json=EMPTY_ACTIVE_TARGETS_RESPONSE,
    )

    cleaned_up = cleanup_completed_projects(config, api)

    assert cleaned_up == 1

    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as conn:
        row = conn.execute("SELECT enabled FROM target").fetchone()
        assert row["enabled"] == 0

    state_db_path = state.state_db_path_for(config.scheduler_db_path)
    with state.open_state_db(state_db_path) as conn:
        assert state.find_target_link(conn, 5) is None


@responses.activate
def test_cleanup_leaves_still_active_targets_alone(telescope_config_yaml):
    config = TelescopeConfig.load(telescope_config_yaml)
    api = ObservatoryApiClient(config.api_base_url, config.api_key)

    responses.get(
        f"{config.api_base_url}/api/v1/telescopes/{config.slug}/active_targets",
        json=ACTIVE_TARGETS_RESPONSE,
    )
    sync_targets_into_scheduler(config, api)

    responses.get(
        f"{config.api_base_url}/api/v1/telescopes/{config.slug}/active_targets",
        json=ACTIVE_TARGETS_RESPONSE,
    )
    cleaned_up = cleanup_completed_projects(config, api)

    assert cleaned_up == 0

    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as conn:
        row = conn.execute("SELECT enabled FROM target").fetchone()
        assert row["enabled"] == 1
