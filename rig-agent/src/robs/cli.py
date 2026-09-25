"""Command-line entry points, meant to be called from NINA's Advanced
Sequencer (as an "External Script" instruction) or from cron/Task
Scheduler for the periodic jobs. See README.md for wiring instructions.
"""

from __future__ import annotations

import logging
import sys

import click

from .api_client import ApiError
from .cleanup import cleanup_completed_projects
from .config import ConfigError, TelescopeConfig
from .end_of_night import end_of_night as run_end_of_night
from .hub import Hub
from .logging_config import configure_logging
from .scheduler_db import SchedulerSchemaError, ensure_schema_compatible, open_scheduler_db, per_project_columns_available
from .sync import managed_target_ids, sync_progress_to_api, sync_targets_into_scheduler

logger = logging.getLogger(__name__)


@click.group()
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
def main(verbose: bool):
    configure_logging(verbose)


@main.command("roof-open")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True), help="Telescope config YAML.")
def roof_open(config_path: str):
    """Fetch active targets and schedule them in NINA Target Scheduler."""
    config, hub = _load(config_path)
    try:
        count = sync_targets_into_scheduler(config, hub)
    except (SchedulerSchemaError, ValueError) as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)
    hub.session_event("roof_open", managed_target_ids(config))
    hub.heartbeat({"last_command": "roof-open", "targets": count})
    click.echo(f"Synced {count} target(s) into Target Scheduler for {config.slug}.")


@main.command("sync-progress")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def sync_progress(config_path: str):
    """Report Target Scheduler's accepted-frame counts back to Rails."""
    config, hub = _load(config_path)
    count = sync_progress_to_api(config, hub)
    hub.heartbeat({"last_command": "sync-progress", "targets": count})
    click.echo(f"Reported progress for {count} target(s) for {config.slug}.")


@main.command("cleanup")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def cleanup(config_path: str):
    """Retire targets Rails no longer considers active out of Target Scheduler."""
    config, hub = _load(config_path)
    count = cleanup_completed_projects(config, hub)
    click.echo(f"Cleaned up {count} target(s) for {config.slug}.")


@main.command("end-of-night")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def end_of_night(config_path: str):
    """End of the NINA sequence: final progress sync and session end (Altair
    pipeline), or S3 upload + optional stacking (legacy), then clean up."""
    config, hub = _load(config_path)
    result = run_end_of_night(config, hub)
    if result["pipeline"] == "altair":
        click.echo(f"Reported {result['progress_reported']} target(s); {result['signal']}; cleaned up {result['cleaned_up']} for {config.slug}.")
    else:
        for published in result["published"]:
            click.echo(f"  target {published['target_id']}: {published['sub_count']} sub(s) published")
        click.echo(f"Published {len(result['published'])} target(s), cleaned up {result['cleaned_up']} completed project(s) for {config.slug}.")
    hub.heartbeat({"last_command": "end-of-night"})


@main.command("session-end")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def session_end(config_path: str):
    """Only signal the end of the session (Hub event, or Altair's marker when standalone)."""
    config, hub = _load(config_path)
    targets = managed_target_ids(config)
    if config.hub_enabled:
        hub.session_event("session_end", targets)
        click.echo(f"session_end sent to the Hub for {config.slug}.")
    else:
        click.echo(f"Wrote {hub.write_session_end_marker(targets)}")


@main.command("check-config")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def check_config(config_path: str):
    """Hub reachable and key accepted, timezone matches the Hub telescope, folders and database present."""
    config, hub = _load(config_path)
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        click.echo(f"[{'ok' if passed else 'FAIL'}] {label}{': ' + detail if detail else ''}")

    check("subs_dir exists", config.subs_dir.is_dir(), str(config.subs_dir))
    check("Target Scheduler database exists", config.scheduler_db_path.is_file(), str(config.scheduler_db_path))
    if config.scheduler_db_path.is_file() and config.ts_project_mode == "per_hub_project":
        with open_scheduler_db(config.scheduler_db_path) as conn:
            check("Target Scheduler supports per-project settings", per_project_columns_available(conn),
                  "falls back to a single managed project otherwise")
    check("data_pipeline", True, config.data_pipeline + ("" if config.altair_mode else " (deprecated: use altair)"))
    if config.hub_enabled:
        try:
            response = hub.api.active_targets_response(config.slug)
            check("Hub reachable, key accepted", True, config.api_base_url)
            hub_tz = (response.get("telescope") or {}).get("timezone")
            check("timezone matches the Hub telescope", bool(config.timezone) and config.timezone == hub_tz,
                  f"config {config.timezone or 'unset'}, Hub {hub_tz or 'unknown'}")
        except (ApiError, OSError) as e:
            check("Hub reachable, key accepted", False, str(e))
    else:
        check("targets_file readable", bool(config.targets_file and config.targets_file.is_file()), str(config.targets_file))
        check("Altair marker folder", True, str(config.marker_dir))
    click.echo("NINA must save into subs_dir with the same file pattern Altair's raw_root expects (SPEC §4.2).")
    sys.exit(0 if ok else 1)


@main.command("check-schema")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def check_schema(config_path: str):
    """Verify the local Target Scheduler database matches scheduler_schema.py."""
    config, _hub = _load(config_path)
    try:
        with open_scheduler_db(config.scheduler_db_path) as conn:
            ensure_schema_compatible(conn)
            per_project = per_project_columns_available(conn)
    except SchedulerSchemaError as e:
        click.echo(f"Schema mismatch: {e}", err=True)
        sys.exit(1)
    click.echo(f"{config.scheduler_db_path} matches the expected Target Scheduler schema.")
    if not per_project:
        click.echo("Note: per-project columns are missing; ts_project_mode per_hub_project falls back to a single managed project.")


def _load(config_path: str) -> tuple[TelescopeConfig, Hub]:
    try:
        config = TelescopeConfig.load(config_path)
    except ConfigError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    return config, Hub.for_config(config)


if __name__ == "__main__":
    main()
