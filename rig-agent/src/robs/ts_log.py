"""Which target Target Scheduler just started, read from the plugin's own log.

A NINA External Script instruction isn't told which target the scheduler
picked, and the plugin keeps no "current target" in its database. It does log
the `TargetScheduler-NewTargetStart` and `TargetScheduler-TargetStart`
messages it publishes to other plugins, with the target name as the content:

    2026-10-10T20:01:13.5082|DEBUG|TSPublisher.cs|Publish|29|publishing message to 'TargetScheduler-NewTargetStart', message:
        content: #34 M31
        ...

Those lines are written at the plugin's DEBUG log level (its default), to
`Logs/TS-*.log` beside `schedulerdb.sqlite`. At a quieter log level there is
nothing to read and no target is reported.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from .config import TelescopeConfig

# A start older than this belongs to an earlier target, not the one starting now.
MAX_AGE = timedelta(minutes=30)

_START = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)[^|]*\|.*publishing message to 'TargetScheduler-(?:New)?TargetStart'")
_CONTENT = re.compile(r"^\s+content:\s*(.*\S)\s*$")
_HUB_ID = re.compile(r"^#(\d+) ")


def latest_log(config: TelescopeConfig) -> Path | None:
    logs = list((config.scheduler_db_path.parent / "Logs").glob("TS-*.log"))
    return max(logs, key=lambda path: path.stat().st_mtime) if logs else None


def last_started_target(config: TelescopeConfig, now: datetime | None = None) -> str | None:
    """The name of the target the plugin most recently started, if that was
    within MAX_AGE (log times are the PC's local time)."""
    path = latest_log(config)
    if path is None:
        return None
    started_at = name = None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for index, line in enumerate(lines):
        start = _START.match(line)
        content = _CONTENT.match(lines[index + 1]) if start and index + 1 < len(lines) else None
        if content:
            started_at, name = datetime.fromisoformat(start.group(1)), content.group(1)
    if name is None or abs((now or datetime.now()) - started_at) > MAX_AGE:
        return None
    return name


def hub_target_id(name: str) -> int | None:
    """The Hub target id in a NINA name ("#<id> <name>"), or None for a target
    that didn't come from the Hub."""
    match = _HUB_ID.match(name)
    return int(match.group(1)) if match else None
