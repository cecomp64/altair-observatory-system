"""Pulling the Hub config (SPEC §17, §6.2 of the system design): cached in
``hub_cache`` so resolution keeps working while the Hub is unreachable."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from observatory_contracts.models.processing.config_response import ProcessingConfigResponse

from altair.catalog.db import Catalog, now_iso
from altair.hub.client import HubClient

CACHE_KEY = "config"


@dataclass
class HubConfig:
    raw: dict[str, Any]
    telescopes: dict[str, dict] = field(default_factory=dict)
    trains: dict[tuple[str, str], dict] = field(default_factory=dict)
    targets: dict[int, dict] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "HubConfig":
        ProcessingConfigResponse.model_validate(payload)  # the contract, before anything relies on it
        config = cls(raw=payload)
        for telescope in payload["telescopes"]:
            config.telescopes[telescope["slug"]] = telescope
            for train in telescope["optical_trains"]:
                config.trains[(telescope["slug"], train["key"])] = train
        config.targets = {t["id"]: t for t in payload["targets"]}
        return config

    @property
    def api_revision(self) -> int:
        return int(self.raw.get("api_revision", 1))

    def train(self, telescope: str, key: str) -> dict | None:
        return self.trains.get((telescope, key))

    def fov_diagonal_deg(self, telescope: str, key: str) -> float | None:
        train = self.train(telescope, key)
        if not train or not train.get("focal_length_mm") or not train.get("pixel_size_um"):
            return None
        scale = 206.265 * train["pixel_size_um"] / train["focal_length_mm"] / 3600.0
        return math.hypot(train["sensor_width_px"] * scale, train["sensor_height_px"] * scale)

    def canonical_filter(self, telescope: str, key: str, raw: str | None) -> str | None:
        """Hub filter names and aliases first (case-insensitive), None if unknown."""
        train = self.train(telescope, key)
        if not train or raw is None:
            return None
        needle = str(raw).strip().lower()
        for f in train.get("filters", []):
            if f["name"].lower() == needle:
                return f["name"]
        for f in train.get("filters", []):
            if needle in (a.lower() for a in f.get("aliases", [])):
                return f["name"]
        return None

    def processing_settings(self, target_id: int) -> dict[str, Any]:
        return (self.targets.get(target_id) or {}).get("processing_settings", {})

    def equipment_event(self, event_id: int) -> dict | None:
        return next((e for e in self.raw.get("equipment_events", []) if e["id"] == event_id), None)


def load_cached(catalog: Catalog) -> HubConfig | None:
    row = catalog.one("SELECT payload_json FROM hub_cache WHERE key = ?", (CACHE_KEY,))
    return HubConfig.from_payload(json.loads(row["payload_json"])) if row else None


def pull(catalog: Catalog, client: HubClient) -> tuple[HubConfig, bool]:
    """GET /processing/config with If-None-Match. Returns (config, changed)."""
    row = catalog.one("SELECT etag FROM hub_cache WHERE key = ?", (CACHE_KEY,))
    payload, etag = client.get_config(row["etag"] if row else None)
    catalog.set_state("last_config_pull_at", now_iso())
    if payload is None:
        cached = load_cached(catalog)
        if cached:
            return cached, False
        payload, etag = client.get_config(None)
    config = HubConfig.from_payload(payload)
    catalog.execute(
        "INSERT OR REPLACE INTO hub_cache(key, etag, payload_json, fetched_at) VALUES (?, ?, ?, ?)",
        (CACHE_KEY, etag, json.dumps(payload), now_iso()),
    )
    return config, True
