"""Per-telescope worker configuration.

Each telescope's control PC gets a small YAML file describing how to
reach the Hub API and where NINA's Target Scheduler database and subs
live locally. See config/example.telescope.yml.

Every value can be overridden by an environment variable
`ROBS_<TELESCOPE_SLUG>_<FIELD>` (upper-cased), which is handy for
keeping the API key out of the YAML file entirely.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Raised when a telescope config file is missing required fields."""


TS_PROJECT_MODES = ("per_hub_project", "single")


@dataclass
class TelescopeConfig:
    slug: str
    api_base_url: str | None
    api_key: str | None
    scheduler_db_path: Path
    subs_dir: Path
    # The GUID of the NINA equipment profile Target Scheduler should
    # file projects/exposure templates under. Find it in NINA's Options
    # > General tab, or in the plugin's own settings.
    nina_profile_id: str | None = None
    # One Target Scheduler project per Hub project, or everything in one.
    ts_project_mode: str = "per_hub_project"
    # Standalone (§3.6.3): no Hub; targets come from targets_file and
    # progress/session events go to a local JSON-lines log.
    hub_enabled: bool = True
    targets_file: Path | None = None
    # Session-end marker for Altair, standalone only.
    altair_marker_dir: Path | None = None
    # IANA timezone of the site; checked against the Hub telescope.
    timezone: str | None = None

    @property
    def marker_dir(self) -> Path:
        return self.altair_marker_dir or (self.subs_dir / "_altair")

    @property
    def event_log_path(self) -> Path:
        return self.scheduler_db_path.parent / f"robs_{self.slug}_events.jsonl"

    @classmethod
    def load(cls, path: str | Path) -> "TelescopeConfig":
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")

        raw = yaml.safe_load(path.read_text()) or {}
        slug = raw.get("slug")
        if not slug:
            raise ConfigError(f"{path}: 'slug' is required")

        def env_override(field_name: str, default: Any) -> Any:
            env_key = f"ROBS_{slug.upper().replace('-', '_')}_{field_name.upper()}"
            return os.environ.get(env_key, default)

        ts_project_mode = raw.get("ts_project_mode", "per_hub_project")
        if ts_project_mode not in TS_PROJECT_MODES:
            raise ConfigError(f"{path}: ts_project_mode must be one of {', '.join(TS_PROJECT_MODES)}")
        hub_enabled = bool((raw.get("hub") or {}).get("enabled", True))
        targets_file = raw.get("targets_file")

        api_base_url = env_override("api_base_url", raw.get("api_base_url"))
        api_key = env_override("api_key", raw.get("api_key"))
        if hub_enabled and (not api_base_url or not api_key):
            raise ConfigError(f"{path}: 'api_base_url' and 'api_key' are required")
        if not hub_enabled and not targets_file:
            raise ConfigError(f"{path}: 'targets_file' is required when hub.enabled is false")

        scheduler_db_path = raw.get("scheduler_db_path")
        subs_dir = raw.get("subs_dir")
        if not scheduler_db_path or not subs_dir:
            raise ConfigError(f"{path}: 'scheduler_db_path' and 'subs_dir' are required")
        # Altair collects, archives and processes the frames (§8.3); the rig
        # agent never uploads or stacks. Old settings are ignored with a warning.
        for obsolete in ("data_pipeline", "s3_bucket", "s3_prefix", "aws_region", "stacking"):
            if obsolete in raw:
                logger.warning("%s: '%s' is no longer used and is ignored", path, obsolete)

        return cls(
            slug=slug,
            api_base_url=api_base_url.rstrip("/") if api_base_url else None,
            api_key=api_key,
            scheduler_db_path=Path(scheduler_db_path).expanduser(),
            subs_dir=Path(subs_dir).expanduser(),
            nina_profile_id=env_override("nina_profile_id", raw.get("nina_profile_id")),
            ts_project_mode=ts_project_mode,
            hub_enabled=hub_enabled,
            targets_file=Path(targets_file).expanduser() if targets_file else None,
            altair_marker_dir=Path(raw["altair_marker_dir"]).expanduser() if raw.get("altair_marker_dir") else None,
            timezone=raw.get("timezone"),
        )
