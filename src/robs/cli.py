"""Command-line entry points, meant to be called from NINA's Advanced
Sequencer (as an "External Script" instruction) or from cron/Task
Scheduler for the periodic jobs. See README.md for wiring instructions.
"""

from __future__ import annotations

import logging
import sys

import click

from .api_client import ObservatoryApiClient
from .cleanup import cleanup_completed_projects
from .config import ConfigError, TelescopeConfig
from .end_of_night import publish_night
from .logging_config import configure_logging
from .scheduler_db import SchedulerSchemaError, ensure_schema_compatible, open_scheduler_db
from .sync import sync_progress_to_api, sync_targets_into_scheduler

logger = logging.getLogger(__name__)


@click.group()
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
def main(verbose: bool):
    configure_logging(verbose)


@main.command("roof-open")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True), help="Telescope config YAML.")
def roof_open(config_path: str):
    """Fetch active targets and schedule them in NINA Target Scheduler."""
    config, api = _load(config_path)
    try:
        count = sync_targets_into_scheduler(config, api)
    except (SchedulerSchemaError, ValueError) as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)
    click.echo(f"Synced {count} target(s) into Target Scheduler for {config.slug}.")


@main.command("sync-progress")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def sync_progress(config_path: str):
    """Report Target Scheduler's accepted-frame counts back to Rails."""
    config, api = _load(config_path)
    count = sync_progress_to_api(config, api)
    click.echo(f"Reported progress for {count} target(s) for {config.slug}.")


@main.command("cleanup")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def cleanup(config_path: str):
    """Retire targets Rails no longer considers active out of Target Scheduler."""
    config, api = _load(config_path)
    count = cleanup_completed_projects(config, api)
    click.echo(f"Cleaned up {count} target(s) for {config.slug}.")


@main.command("end-of-night")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def end_of_night(config_path: str):
    """Publish tonight's subs (and optional stacks) to S3, then clean up."""
    config, api = _load(config_path)
    results = publish_night(config, api)
    for result in results:
        click.echo(f"  target {result['target_id']}: {result['sub_count']} sub(s) published")
    count = cleanup_completed_projects(config, api)
    click.echo(f"Published {len(results)} target(s), cleaned up {count} completed project(s) for {config.slug}.")


@main.command("check-schema")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def check_schema(config_path: str):
    """Verify the local Target Scheduler database matches scheduler_schema.py."""
    config, _api = _load(config_path)
    try:
        with open_scheduler_db(config.scheduler_db_path) as conn:
            ensure_schema_compatible(conn)
    except SchedulerSchemaError as e:
        click.echo(f"Schema mismatch: {e}", err=True)
        sys.exit(1)
    click.echo(f"{config.scheduler_db_path} matches the expected Target Scheduler schema.")


def _load(config_path: str) -> tuple[TelescopeConfig, ObservatoryApiClient]:
    try:
        config = TelescopeConfig.load(config_path)
    except ConfigError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    api = ObservatoryApiClient(config.api_base_url, config.api_key)
    return config, api


if __name__ == "__main__":
    main()
