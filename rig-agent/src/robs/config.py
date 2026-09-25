"""Per-telescope worker configuration.

Each telescope's control PC gets a small YAML file describing how to
reach the Rails API, where NINA's Target Scheduler database and subs
live locally, and (optionally) where to publish files and how to run
calibration/stacking. See config/example.telescope.yml.

Every value can be overridden by an environment variable
`ROBS_<TELESCOPE_SLUG>_<FIELD>` (upper-cased), which is handy for
keeping the API key out of the YAML file entirely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """Raised when a telescope config file is missing required fields."""


@dataclass
class StackingConfig:
    enabled: bool = False
    backend: str = "siril"  # "siril" | "pixinsight" | "none"
    executable_path: str | None = None
    master_frames_dir: str | None = None
    output_dir: str | None = None


@dataclass
class TelescopeConfig:
    slug: str
    api_base_url: str
    api_key: str
    scheduler_db_path: Path
    subs_dir: Path
    s3_bucket: str
    s3_prefix: str = ""
    aws_region: str | None = None
    # The GUID of the NINA equipment profile Target Scheduler should
    # file projects/exposure templates under. Find it in NINA's Options
    # > General tab, or in the plugin's own settings.
    nina_profile_id: str | None = None
    stacking: StackingConfig = field(default_factory=StackingConfig)

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

        api_base_url = env_override("api_base_url", raw.get("api_base_url"))
        api_key = env_override("api_key", raw.get("api_key"))
        if not api_base_url or not api_key:
            raise ConfigError(f"{path}: 'api_base_url' and 'api_key' are required")

        scheduler_db_path = raw.get("scheduler_db_path")
        subs_dir = raw.get("subs_dir")
        s3_bucket = env_override("s3_bucket", raw.get("s3_bucket"))
        if not scheduler_db_path or not subs_dir or not s3_bucket:
            raise ConfigError(
                f"{path}: 'scheduler_db_path', 'subs_dir', and 's3_bucket' are required"
            )

        stacking_raw = raw.get("stacking") or {}
        stacking = StackingConfig(
            enabled=bool(stacking_raw.get("enabled", False)),
            backend=stacking_raw.get("backend", "siril"),
            executable_path=stacking_raw.get("executable_path"),
            master_frames_dir=stacking_raw.get("master_frames_dir"),
            output_dir=stacking_raw.get("output_dir"),
        )

        return cls(
            slug=slug,
            api_base_url=api_base_url.rstrip("/"),
            api_key=api_key,
            scheduler_db_path=Path(scheduler_db_path).expanduser(),
            subs_dir=Path(subs_dir).expanduser(),
            s3_bucket=s3_bucket,
            s3_prefix=raw.get("s3_prefix", "").strip("/"),
            aws_region=raw.get("aws_region"),
            nina_profile_id=env_override("nina_profile_id", raw.get("nina_profile_id")),
            stacking=stacking,
        )
