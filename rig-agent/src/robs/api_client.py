"""Thin client for the Hub's JSON API.

The contract lives in contracts/schemas (docs/SYSTEM_ARCHITECTURE.md §5.2);
responses are parsed with the observatory-contracts models. Every method
raises `ApiError` on a non-2xx response.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import requests

logger = logging.getLogger(__name__)


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ObservatoryApiClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 15.0, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, headers=self._headers(), timeout=self.timeout, **kwargs)

        if not response.ok:
            body = _safe_json(response)
            message = body.get("error") if isinstance(body, dict) else response.text
            raise ApiError(f"{method} {path} failed ({response.status_code}): {message}", response.status_code)

        return _safe_json(response)

    def active_targets(self, telescope_slug: str) -> list[dict[str, Any]]:
        """Targets the worker should have scheduled in NINA right now."""
        return self.active_targets_response(telescope_slug).get("targets", [])

    def active_targets_response(self, telescope_slug: str) -> dict[str, Any]:
        """The whole response, including the telescope (timezone, api_revision 1)."""
        return self._request("GET", f"/api/v1/telescopes/{telescope_slug}/active_targets")

    def post_session_event(self, telescope_slug: str, event: str, at: str, night: str, target_ids: Iterable[int] = ()) -> dict[str, Any]:
        """roof_open / roof_close / session_end (§5.2). session_end makes the
        Hub queue night_ready for Altair."""
        body = {"event": event, "at": at, "night": night, "target_ids": list(target_ids)}
        return self._request("POST", f"/api/v1/telescopes/{telescope_slug}/sessions", json=body)

    def heartbeat(self, status: dict[str, Any]) -> dict[str, Any]:
        from observatory_contracts import API_REVISION

        from . import __version__

        body = {"agent": "robs", "version": __version__, "api_revision": API_REVISION, "status": status}
        return self._request("POST", "/api/v1/heartbeat", json=body)

    def update_progress(
        self,
        target_id: int,
        exposure_plans: Iterable[dict[str, Any]],
        status: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"exposure_plans": list(exposure_plans)}
        if status:
            payload["status"] = status
        return self._request("PATCH", f"/api/v1/targets/{target_id}/progress", json=payload)

    def add_file(
        self,
        target_id: int,
        url: str,
        kind: str = "sub",
        filter: str | None = None,
        captured_at: str | None = None,
    ) -> dict[str, Any]:
        payload = {"url": url, "kind": kind, "filter": filter, "captured_at": captured_at}
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._request("POST", f"/api/v1/targets/{target_id}/files", json=payload)

    def add_event(self, target_id: int, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {"event_type": event_type, "payload": payload or {}}
        return self._request("POST", f"/api/v1/targets/{target_id}/events", json=body)


def _safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {}
