"""HTTP client for the Hub API (docs/SYSTEM_ARCHITECTURE.md §5).

Errors are split the way the outbox needs them (SPEC §17.3): network
failures and 5xx are ``HubUnavailable`` (retry forever), 4xx are
``HubRejected`` (retry a few times, then park).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from altair import __version__

API_PREFIX = "/api/v1"


class HubError(Exception):
    pass


class HubUnavailable(HubError):
    """Network error, timeout or 5xx: try again later."""


class HubRejected(HubError):
    """4xx: the Hub refused the request."""

    def __init__(self, status: int, message: str, details: Any = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.details = details


class HubClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + API_PREFIX,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json", "User-Agent": f"altair/{__version__}"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise HubUnavailable(f"{method} {path}: {exc}") from exc
        if response.status_code >= 500:
            raise HubUnavailable(f"{method} {path}: HTTP {response.status_code}")
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text}
            raise HubRejected(response.status_code, str(body.get("error", "")), body.get("details"))
        return response

    # ── Config and commands ──────────────────────────────────────────────
    def get_config(self, etag: str | None = None) -> tuple[dict | None, str | None]:
        """Returns (payload, etag); payload is None on 304 Not Modified."""
        headers = {"If-None-Match": etag} if etag else {}
        response = self._request("GET", "/processing/config", headers=headers)
        if response.status_code == 304:
            return None, etag
        return response.json(), response.headers.get("ETag")

    def pending_commands(self) -> list[dict]:
        return self._request("GET", "/processing/commands", params={"state": "pending"}).json()

    def ack_command(self, command_id: int, state: str, result: dict | None) -> None:
        self._request("POST", f"/processing/commands/{command_id}/ack", json={"state": state, "result": result})

    # ── Reports ──────────────────────────────────────────────────────────
    def post_frames(self, frames: list[dict]) -> list[dict]:
        return self._request("POST", "/processing/frames:batch", json={"frames": frames}).json()["results"]

    def patch_frames(self, frames: list[dict]) -> list[dict]:
        return self._request("PATCH", "/processing/frames:batch", json={"frames": frames}).json()["results"]

    def put_night(self, optical_train: str, night: str, body: dict) -> None:
        self._request("PUT", f"/processing/nights/{optical_train}/{night}", json=body)

    def night_digest(self, optical_train: str, night: str) -> dict:
        return self._request("GET", f"/processing/nights/{optical_train}/{night}/digest").json()

    def put_calibration_master(self, altair_id: int, body: dict) -> None:
        self._request("PUT", f"/processing/calibration_masters/{altair_id}", json=body)

    def put_data_product(self, kind: str, altair_id: int, metadata: dict, attachments: dict[str, str] | None = None) -> None:
        files: dict[str, Any] = {"metadata": (None, json.dumps(metadata), "application/json")}
        handles = []
        try:
            for name, path in (attachments or {}).items():
                if path and Path(path).is_file():
                    handle = open(path, "rb")  # noqa: SIM115 - closed below
                    handles.append(handle)
                    files[name] = (Path(path).name, handle, "image/jpeg")
            self._request("PUT", f"/processing/data_products/{kind}/{altair_id}", files=files)
        finally:
            for handle in handles:
                handle.close()

    def put_issue(self, fingerprint: str, body: dict) -> None:
        from urllib.parse import quote

        self._request("PUT", f"/processing/issues/{quote(fingerprint, safe='')}", json=body)

    def put_job(self, altair_id: int, body: dict) -> None:
        self._request("PUT", f"/processing/jobs/{altair_id}", json=body)

    def heartbeat(self, status: dict) -> dict:
        from observatory_contracts import API_REVISION

        body = {"agent": "altair", "version": __version__, "api_revision": API_REVISION, "status": status}
        return self._request("POST", "/heartbeat", json=body).json()
