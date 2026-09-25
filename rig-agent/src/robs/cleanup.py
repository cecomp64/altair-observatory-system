"""Retires completed/cancelled projects out of Target Scheduler.

Rails automatically flips a target to `completed` once every exposure
plan hits its desired count (see `sync_progress_to_api`, and
`Api::V1::TargetsController#progress` in the queueing system). Once
that happens it stops appearing in `GET .../active_targets`. This
module's job is simply to notice that and disable the corresponding
row in Target Scheduler so NINA stops trying to image it, then drop
our local state so it won't get re-synced.
"""

from __future__ import annotations

import logging

from .api_client import ObservatoryApiClient
from .config import TelescopeConfig
from . import scheduler_db
from . import state

logger = logging.getLogger(__name__)


def cleanup_completed_projects(config: TelescopeConfig, api: ObservatoryApiClient) -> int:
    """Disables scheduler targets no longer active in Rails.

    Returns the number of targets cleaned up.
    """
    still_active_ids = {t["id"] for t in api.active_targets(config.slug)}
    state_db_path = state.state_db_path_for(config.scheduler_db_path)
    cleaned_up = 0

    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as sched_conn:
        with state.open_state_db(state_db_path) as state_conn:
            for target_link in state.all_target_links(state_conn):
                rails_target_id = target_link["rails_target_id"]
                if rails_target_id in still_active_ids:
                    continue

                scheduler_db.disable_target(sched_conn, target_link["scheduler_target_id"])
                state.remove_target_link(state_conn, rails_target_id)
                cleaned_up += 1
                logger.info(
                    "Retired target %d (scheduler target %d) for %s",
                    rails_target_id,
                    target_link["scheduler_target_id"],
                    config.slug,
                )

    return cleaned_up
