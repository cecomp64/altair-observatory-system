"""The NINA equipment profile `nina_profile_id` names.

NINA keeps one `<guid>.profile` XML file per profile in a `Profiles` folder
beside Target Scheduler's `SchedulerPlugin` folder. Target Scheduler only
shows rows whose profileId is a real profile, and can only image a filter
the profile's filter wheel has, so both are worth checking before a night.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from .config import TelescopeConfig

logger = logging.getLogger(__name__)


def profiles_dir(config: TelescopeConfig) -> Path:
    return config.scheduler_db_path.parent.parent / "Profiles"


def profile_path(config: TelescopeConfig) -> Path | None:
    """The profile's file, or None when the id is unset or matches no profile."""
    if not config.nina_profile_id:
        return None
    path = profiles_dir(config) / f"{config.nina_profile_id}.profile"
    return path if path.is_file() else None


def filter_names(path: Path) -> set[str]:
    """Filter names of the profile's filter wheel (empty if it can't be read)."""
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        logger.warning("Couldn't read NINA profile %s: %s", path, exc)
        return set()
    return {
        name.text
        for wheel in root.iter() if _local(wheel.tag) == "FilterWheelFilters"
        for name in wheel.iter() if _local(name.tag) == "_name" and name.text
    }


def missing_filters(config: TelescopeConfig, targets: Iterable[dict[str, Any]]) -> list[str]:
    """Filters the targets' exposure plans use that the profile's filter wheel
    lacks. Empty when the profile can't be found or lists no filters."""
    path = profile_path(config)
    known = filter_names(path) if path else set()
    if not known:
        return []
    wanted = {plan["filter"] for target in targets for plan in target.get("exposure_plans", [])}
    return sorted(wanted - known)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
