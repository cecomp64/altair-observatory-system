"""End of night (§8.3).

The rig agent uploads and stacks nothing: Altair collects, archives and
processes the frames. At the end of the sequence it runs a final progress
sync, reports `session_end` to the Hub (which queues `night_ready` for
Altair) or, with no Hub, writes Altair's session-end marker, then cleans
up. One NINA end-of-sequence script covers both the rig agent and Altair.
"""

from __future__ import annotations

from .config import TelescopeConfig
from .hub import Hub


def end_of_night(config: TelescopeConfig, hub: Hub) -> dict:
    from .cleanup import cleanup_completed_projects
    from .sync import managed_target_ids, sync_progress_to_api

    reported = sync_progress_to_api(config, hub)
    target_ids = managed_target_ids(config)
    if config.hub_enabled:
        hub.session_event("session_end", target_ids)
        signal = "session_end sent to the Hub"
    else:
        signal = f"marker {hub.write_session_end_marker(target_ids)}"
    cleaned = cleanup_completed_projects(config, hub)
    return {"progress_reported": reported, "signal": signal, "cleaned_up": cleaned}
