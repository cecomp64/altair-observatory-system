"""Two-way sync between the Rails queue and NINA Target Scheduler.

`sync_targets_into_scheduler` — run when the roof opens (NINA External
Script sequencer step): pulls active targets from the Rails API and
upserts them into Target Scheduler so NINA images them tonight.

`sync_progress_to_api` — run periodically through the night (or at
roof close): reads each managed target's accepted-frame counts back out
of Target Scheduler and reports them to Rails, which triggers alerts.
"""

from __future__ import annotations

import logging

from .api_client import ObservatoryApiClient
from .config import TelescopeConfig
from . import scheduler_db
from . import state

logger = logging.getLogger(__name__)


def sync_targets_into_scheduler(config: TelescopeConfig, api: ObservatoryApiClient) -> int:
    """Pulls active targets and upserts them into Target Scheduler.

    Returns the number of targets synced.
    """
    if not config.nina_profile_id:
        raise ValueError(f"{config.slug}: 'nina_profile_id' is not configured")

    targets = api.active_targets(config.slug)
    logger.info("Fetched %d active target(s) for %s", len(targets), config.slug)

    state_db_path = state.state_db_path_for(config.scheduler_db_path)

    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as sched_conn:
        scheduler_db.ensure_schema_compatible(sched_conn)
        project_id = scheduler_db.get_or_create_project(sched_conn, config.nina_profile_id)

        with state.open_state_db(state_db_path) as state_conn:
            for target in targets:
                scheduler_target_id = scheduler_db.upsert_target(
                    sched_conn, project_id, _scheduler_target_name(target), target["ra_deg"], target["dec_deg"]
                )
                state.link_target(state_conn, target["id"], scheduler_target_id, project_id)

                for plan in target["exposure_plans"]:
                    template_id = scheduler_db.get_or_create_exposure_template(
                        sched_conn, config.nina_profile_id, plan["filter"], plan["exposure_seconds"]
                    )
                    scheduler_plan_id = scheduler_db.upsert_exposure_plan(
                        sched_conn, scheduler_target_id, template_id, plan["desired_count"]
                    )
                    state.link_exposure_plan(state_conn, plan["id"], target["id"], scheduler_plan_id, plan["filter"])

    return len(targets)


def sync_progress_to_api(config: TelescopeConfig, api: ObservatoryApiClient) -> int:
    """Reads accepted-frame counts out of Target Scheduler and reports them.

    Returns the number of targets whose progress was reported.
    """
    state_db_path = state.state_db_path_for(config.scheduler_db_path)
    reported = 0

    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as sched_conn:
        with state.open_state_db(state_db_path) as state_conn:
            for target_link in state.all_target_links(state_conn):
                plan_links = state.exposure_plan_links_for_target(state_conn, target_link["rails_target_id"])
                if not plan_links:
                    continue

                exposure_plans = [
                    {
                        "id": plan_link["rails_exposure_plan_id"],
                        "completed_count": scheduler_db.read_accepted_count(
                            sched_conn, plan_link["scheduler_exposure_plan_id"]
                        ),
                    }
                    for plan_link in plan_links
                ]

                api.update_progress(target_link["rails_target_id"], exposure_plans)
                reported += 1

    logger.info("Reported progress for %d target(s) for %s", reported, config.slug)
    return reported


def _scheduler_target_name(target: dict) -> str:
    # Prefix with the Rails id so re-syncs match the same row even if a
    # member renames the target after it's already scheduled.
    return f"#{target['id']} {target['name']}"
