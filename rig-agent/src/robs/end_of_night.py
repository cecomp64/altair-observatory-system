"""End of night.

With `data_pipeline: altair` (§8.3) the worker uploads and stacks nothing:
it runs a final progress sync, reports `session_end` to the Hub (which
queues `night_ready` for Altair), or, with no Hub, writes Altair's
session-end marker, then cleans up. One NINA end-of-sequence script covers
both the worker and Altair.

With `data_pipeline: legacy` (deprecated, removed after cutover) it
uploads subs to S3, and optionally runs calibration + stacking, publishing
the result too:

Expects `subs_dir` to contain one subdirectory per target, named with
the target's Rails id as a leading token — e.g. `#12 M42/` or `12/`,
which is exactly what NINA will produce if its sequencer's target/
image-file-path pattern includes the scheduler target's name (we name
scheduler targets `#<rails_id> <name>`, see sync._scheduler_target_name).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .api_client import ObservatoryApiClient
from .config import TelescopeConfig
from .hub import Hub
from .s3_publisher import S3Publisher
from .stacking import build_backend

logger = logging.getLogger(__name__)

_TARGET_DIR_RE = re.compile(r"^#?(\d+)")
_KNOWN_FILTERS = ["Luminance", "Red", "Green", "Blue", "Ha", "OIII", "SII", "L", "R", "G", "B"]


def end_of_night(config: TelescopeConfig, hub: Hub, publisher: S3Publisher | None = None) -> dict:
    """The whole end-of-night step for either data pipeline."""
    from .cleanup import cleanup_completed_projects
    from .sync import managed_target_ids, sync_progress_to_api

    if config.altair_mode:
        reported = sync_progress_to_api(config, hub)
        target_ids = managed_target_ids(config)
        if config.hub_enabled:
            hub.session_event("session_end", target_ids)
            signal = "session_end sent to the Hub"
        else:
            signal = f"marker {hub.write_session_end_marker(target_ids)}"
        cleaned = cleanup_completed_projects(config, hub)
        return {"pipeline": "altair", "progress_reported": reported, "signal": signal, "cleaned_up": cleaned}

    logger.warning("data_pipeline: legacy is deprecated: Altair archives and processes frames; set data_pipeline: altair")
    published = publish_night(config, hub.api, publisher)
    cleaned = cleanup_completed_projects(config, hub)
    return {"pipeline": "legacy", "published": published, "cleaned_up": cleaned}


def publish_night(config: TelescopeConfig, api: ObservatoryApiClient, publisher: S3Publisher | None = None) -> list[dict]:
    publisher = publisher or S3Publisher(bucket=config.s3_bucket, prefix=config.s3_prefix, region=config.aws_region)

    if not config.subs_dir.exists():
        logger.warning("subs_dir %s does not exist, nothing to publish", config.subs_dir)
        return []

    published = []
    for target_dir in sorted(p for p in config.subs_dir.iterdir() if p.is_dir()):
        target_id = _extract_target_id(target_dir.name)
        if target_id is None:
            logger.warning("Skipping %s: could not determine a target id from the directory name", target_dir)
            continue

        date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        uploaded = publisher.upload_directory(target_dir, config.slug, str(target_id), date_key)
        for local_path, url in uploaded:
            api.add_file(
                target_id,
                url=url,
                kind="sub",
                filter=_guess_filter(local_path.name),
                captured_at=_file_captured_at(local_path),
            )

        result = {"target_id": target_id, "sub_count": len(uploaded)}

        if config.stacking.enabled and uploaded:
            stacked_url, preview_url = _stack_and_publish(config, api, publisher, target_dir, target_id, date_key)
            result["stacked_url"] = stacked_url
            result["preview_url"] = preview_url

        published.append(result)
        logger.info("Published %s", result)

    return published


def _stack_and_publish(
    config: TelescopeConfig,
    api: ObservatoryApiClient,
    publisher: S3Publisher,
    target_dir: Path,
    target_id: int,
    date_key: str,
) -> tuple[str | None, str | None]:
    backend = build_backend(config.stacking)
    master_frames_dir = Path(config.stacking.master_frames_dir) if config.stacking.master_frames_dir else None
    output_dir = Path(config.stacking.output_dir) if config.stacking.output_dir else target_dir / "stacked"
    output_name = f"target_{target_id}_{date_key}"

    try:
        result = backend.calibrate_and_stack(target_dir, master_frames_dir, output_dir, output_name)
    except Exception:
        logger.exception("Stacking failed for target %d, subs were still published", target_id)
        api.add_event(target_id, "error", {"message": "Stacking failed after subs were published"})
        return None, None

    stacked_url = publisher.upload_file(result.stacked_path, config.slug, str(target_id), date_key, "stacked")
    api.add_file(target_id, url=stacked_url, kind="stacked", captured_at=_file_captured_at(result.stacked_path))

    preview_url = None
    if result.preview_path and result.preview_path.exists():
        preview_url = publisher.upload_file(result.preview_path, config.slug, str(target_id), date_key, "stacked")
        api.add_file(target_id, url=preview_url, kind="preview", captured_at=_file_captured_at(result.preview_path))

    return stacked_url, preview_url


def _extract_target_id(dirname: str) -> int | None:
    match = _TARGET_DIR_RE.match(dirname.strip())
    return int(match.group(1)) if match else None


def _guess_filter(filename: str) -> str | None:
    for filt in _KNOWN_FILTERS:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(filt)}(?![A-Za-z0-9])", filename, re.IGNORECASE):
            return filt
    return None


def _file_captured_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
