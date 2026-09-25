"""Where the worker gets targets and sends reports: the Hub, or (standalone,
§3.6.3) a local targets file and a JSON-lines event log. Everything that
isn't Target Scheduler goes through here."""
from __future__ import annotations

import json
import logging
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .api_client import ApiError, ObservatoryApiClient
from .config import TelescopeConfig

logger = logging.getLogger(__name__)


class Hub:
    def __init__(self, config: TelescopeConfig, api: ObservatoryApiClient | None):
        self.config = config
        self.api = api

    @classmethod
    def for_config(cls, config: TelescopeConfig) -> "Hub":
        api = ObservatoryApiClient(config.api_base_url, config.api_key) if config.hub_enabled else None
        return cls(config, api)

    # ── targets ──────────────────────────────────────────────────────────
    def active_targets(self) -> list[dict[str, Any]]:
        if self.api:
            return self.api.active_targets(self.config.slug)
        data = json.loads(Path(self.config.targets_file).read_text(encoding="utf-8"))
        from observatory_contracts.models.worker.active_targets_response import ActiveTargetsResponse

        ActiveTargetsResponse.model_validate(data)  # same schema as the Hub response
        return data["targets"]

    # ── reports ──────────────────────────────────────────────────────────
    def update_progress(self, target_id: int, exposure_plans: list[dict[str, Any]]) -> None:
        if self.api:
            self.api.update_progress(target_id, exposure_plans)
        else:
            self._log("progress", target_id=target_id, exposure_plans=exposure_plans)

    def session_event(self, event: str, target_ids: Iterable[int] = (), at: datetime | None = None) -> None:
        """Best effort: a failed report must never stop the night."""
        at = at or datetime.now(timezone.utc)
        night = self.night_for(at)
        ids = list(target_ids)
        if not self.api:
            self._log("session", event=event, at=_iso(at), night=night, target_ids=ids)
            return
        try:
            self.api.post_session_event(self.config.slug, event, _iso(at), night, ids)
        except (ApiError, OSError) as exc:
            logger.warning("Couldn't report %s to the Hub: %s", event, exc)

    def heartbeat(self, status: dict[str, Any]) -> None:
        if not self.api:
            return
        try:
            self.api.heartbeat({"telescope": self.config.slug, "data_pipeline": self.config.data_pipeline, **status})
        except (ApiError, OSError) as exc:
            logger.debug("Heartbeat failed: %s", exc)

    def write_session_end_marker(self, target_ids: Iterable[int], at: datetime | None = None) -> Path:
        """Standalone only: the marker Altair watches for (SPEC §4.2)."""
        at = at or datetime.now(timezone.utc)
        local = at.astimezone(ZoneInfo(self.config.timezone)) if self.config.timezone else at
        path = self.config.marker_dir / f"session-end-{local.strftime('%Y%m%dT%H%M%S')}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"host": socket.gethostname(), "at": _iso(at), "telescope": self.config.slug, "night": self.night_for(at),
                "target_ids": list(target_ids)}
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def night_for(self, at: datetime) -> str:
        """Local noon-to-noon night (§4.4), in the site timezone."""
        tz = ZoneInfo(self.config.timezone) if self.config.timezone else timezone.utc
        return (at.astimezone(tz) - timedelta(hours=12)).date().isoformat()

    def _log(self, kind: str, **fields: Any) -> None:
        path = self.config.event_log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": kind, "logged_at": _iso(datetime.now(timezone.utc)), **fields}) + "\n")


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
