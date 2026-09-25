"""Two-way sync between the Hub and NINA Target Scheduler.

`sync_targets_into_scheduler` — run when the roof opens (NINA External
Script sequencer step): pulls active targets and upserts them into Target
Scheduler so NINA images them tonight. With ts_project_mode
per_hub_project, each Hub project becomes its own Target Scheduler project
("#P<id> <name>", the project's priority, the target's minimum altitude);
targets that were in the old single managed project move over with their
accepted counts, and that project is disabled once it is empty (§8.3).

`sync_progress_to_api` — run periodically through the night: reads each
managed target's accepted-frame counts out of Target Scheduler and reports
them (to the Hub, or the local event log when standalone).
"""

from __future__ import annotations

import logging

from . import scheduler_db
from . import scheduler_schema
from . import state
from .api_client import ObservatoryApiClient
from .config import TelescopeConfig
from .hub import Hub

logger = logging.getLogger(__name__)


def _hub(config: TelescopeConfig, api: ObservatoryApiClient | Hub | None) -> Hub:
    return api if isinstance(api, Hub) else Hub(config, api)


def sync_targets_into_scheduler(config: TelescopeConfig, api: ObservatoryApiClient | Hub | None) -> int:
    """Pulls active targets and upserts them into Target Scheduler.

    Returns the number of targets synced.
    """
    if not config.nina_profile_id:
        raise ValueError(f"{config.slug}: 'nina_profile_id' is not configured")

    hub = _hub(config, api)
    targets = hub.active_targets()
    logger.info("Fetched %d active target(s) for %s", len(targets), config.slug)

    state_db_path = state.state_db_path_for(config.scheduler_db_path)

    with scheduler_db.open_scheduler_db(config.scheduler_db_path) as sched_conn:
        scheduler_db.ensure_schema_compatible(sched_conn)
        per_project = config.ts_project_mode == "per_hub_project"
        if per_project and not scheduler_db.per_project_columns_available(sched_conn):
            logger.warning("Target Scheduler lacks %s; using a single managed project (see check-schema)",
                           ", ".join(scheduler_schema.PROJECT_OPTIONAL_COLUMNS.values()))
            per_project = False
        single_project_id = None

        with state.open_state_db(state_db_path) as state_conn:
            for target in targets:
                project = target.get("project")
                if per_project and project:
                    link = state.find_project_link(state_conn, project["id"])
                    project_id = scheduler_db.upsert_hub_project(
                        sched_conn, config.nina_profile_id, project["ts_project_name"], project["priority"],
                        target.get("min_altitude_deg"), existing_id=link["scheduler_project_id"] if link else None,
                    )
                    state.link_project(state_conn, project["id"], project_id)
                else:
                    single_project_id = single_project_id or scheduler_db.get_or_create_project(sched_conn, config.nina_profile_id)
                    project_id = single_project_id

                # A target scheduled before (e.g. in the old single project)
                # moves with its exposure plans and accepted counts.
                existing = state.find_target_link(state_conn, target["id"])
                if existing and existing["scheduler_project_id"] != project_id:
                    scheduler_db.move_target(sched_conn, existing["scheduler_target_id"], project_id)

                scheduler_target_id = scheduler_db.upsert_target(
                    sched_conn, project_id, _scheduler_target_name(target), target["ra_deg"], target["dec_deg"]
                )
                state.link_target(state_conn, target["id"], scheduler_target_id, project_id)

                for plan in target["exposure_plans"]:
                    template_id = scheduler_db.get_or_create_exposure_template(
                        sched_conn, config.nina_profile_id, plan["filter"], plan["exposure_seconds"]
                    )
                    scheduler_plan_id = scheduler_db.upsert_exposure_plan(
                        sched_conn, scheduler_target_id, template_id, plan.get("schedule_count", plan["desired_count"])
                    )
                    state.link_exposure_plan(state_conn, plan["id"], target["id"], scheduler_plan_id, plan["filter"])

        if per_project:
            _retire_single_project(sched_conn, config)

    return len(targets)


def _retire_single_project(sched_conn, config: TelescopeConfig) -> None:
    managed = scheduler_db.find_managed_project(sched_conn, config.nina_profile_id)
    if managed and scheduler_db.enabled_target_count(sched_conn, managed) == 0:
        scheduler_db.set_project_state(sched_conn, managed, scheduler_schema.PROJECT_STATE_INACTIVE)
        logger.info("Disabled the old single managed project; targets now live in per-project Target Scheduler projects")


def sync_progress_to_api(config: TelescopeConfig, api: ObservatoryApiClient | Hub | None) -> int:
    """Reads accepted-frame counts out of Target Scheduler and reports them.

    Returns the number of targets whose progress was reported.
    """
    hub = _hub(config, api)
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

                hub.update_progress(target_link["rails_target_id"], exposure_plans)
                reported += 1

    logger.info("Reported progress for %d target(s) for %s", reported, config.slug)
    return reported


def managed_target_ids(config: TelescopeConfig) -> list[int]:
    with state.open_state_db(state.state_db_path_for(config.scheduler_db_path)) as conn:
        return [row["rails_target_id"] for row in state.all_target_links(conn)]


def _scheduler_target_name(target: dict) -> str:
    # The Hub owns the NINA name ("#<id> <name>", §4.4). NINA writes it into
    # OBJECT, which is how Altair links frames to the target. Older Hubs
    # don't send it; the format is the same.
    return target.get("nina_name") or f"#{target['id']} {target['name']}"
