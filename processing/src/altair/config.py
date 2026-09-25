"""altair.yaml (SPEC §5), the parts the Hub integration needs.

Unknown sections are kept (``extra="allow"``) so one file carries the whole
SPEC configuration while components are built phase by phase.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_HEADER_MAPPING: dict[str, list[str]] = {
    "image_type": ["IMAGETYP"], "telescope": ["TELESCOP"], "camera": ["INSTRUME"], "focal_length": ["FOCALLEN"],
    "filter": ["FILTER"], "target": ["OBJECT"], "exposure": ["EXPOSURE", "EXPTIME"], "gain": ["GAIN"],
    "offset": ["OFFSET"], "sensor_temp": ["CCD-TEMP"], "xbinning": ["XBINNING"], "ybinning": ["YBINNING"],
    "readout_mode": ["READOUTM"], "bayer_pattern": ["BAYERPAT"], "date_obs": ["DATE-OBS"],
    "ra": ["RA", "OBJCTRA"], "dec": ["DEC", "OBJCTDEC"], "width": ["NAXIS1"], "height": ["NAXIS2"],
    "rotation": ["OBJCTROT", "POSANGLE"],
}


class Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class Site(Loose):
    name: str = "Observatory"
    latitude: float
    longitude: float
    timezone: str
    session_rollover_local: str = "12:00"


class Paths(Loose):
    state: str = "C:/ProgramData/Altair/state"
    cache: str | None = None


class Rotator(Loose):
    present: bool = False
    source: Literal["header", "filename"] = "header"
    keywords: list[str] = Field(default_factory=lambda: ["ROTATOR"])
    filename_regex: str | None = None
    units: Literal["degrees", "steps"] = "degrees"


class RigHub(Loose):
    telescope: str
    optical_train: str


class Rig(Loose):
    hub: RigHub | None = None
    host: str | None = None
    raw_root: str | None = None
    telescope: str
    camera: str
    focal_length_mm: float
    focal_length_tolerance_mm: float = 10
    rotator: Rotator = Field(default_factory=Rotator)
    default_filter: str | None = None


class HubResolve(Loose):
    by_header_token: bool = True
    by_name: bool = True
    by_coordinates: bool = True
    max_offset_fov_fraction: float = 0.5


class HubOutbox(Loose):
    batch_size: int = Field(500, ge=1, le=500)
    max_backoff_s: float = 900


class HubPreviews(Loose):
    enabled: bool = True
    long_edge_px: int = 2048
    thumb_px: int = 512
    jpeg_quality: int = 85


class Hub(Loose):
    enabled: bool = False
    base_url: str = ""
    node: str = ""
    credential_target: str = "altair-hub"
    config_poll_s: float = 300
    command_poll_s: float = 60
    unreachable_alert_minutes: float = 60
    require_target_link: bool = True
    resolve: HubResolve = Field(default_factory=HubResolve)
    outbox: HubOutbox = Field(default_factory=HubOutbox)
    previews: HubPreviews = Field(default_factory=HubPreviews)
    frame_headers: Literal["full", "summary"] = "full"

    def api_key(self) -> str | None:
        """The node key: ALTAIR_HUB_API_KEY, else Windows Credential Manager."""
        if key := os.environ.get("ALTAIR_HUB_API_KEY"):
            return key
        try:
            import keyring

            return keyring.get_password(self.credential_target, self.node) or keyring.get_password(self.credential_target, "api_key")
        except Exception:  # noqa: BLE001 - no keyring backend on this machine
            return None


class AltairConfig(Loose):
    site: Site
    paths: Paths = Field(default_factory=Paths)
    rigs: dict[str, Rig] = Field(default_factory=dict)
    aliases: dict[str, dict[str, str]] = Field(default_factory=dict)
    header_mapping: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_HEADER_MAPPING))
    hub: Hub = Field(default_factory=Hub)

    @field_validator("header_mapping")
    @classmethod
    def _merge_defaults(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        return {**DEFAULT_HEADER_MAPPING, **value}

    @property
    def catalog_path(self) -> Path:
        return Path(self.paths.state) / "catalog.sqlite"

    def rig_for_train(self, telescope: str, optical_train: str) -> str | None:
        for name, rig in self.rigs.items():
            if rig.hub and rig.hub.telescope == telescope and rig.hub.optical_train == optical_train:
                return name
        return None


def load(path: str | os.PathLike[str]) -> AltairConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AltairConfig.model_validate(data)
