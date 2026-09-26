from __future__ import annotations

from datetime import datetime, timedelta, timezone

from altair.hub.config_sync import HubConfig
from altair.hub.sync import HubSync
from altair.index.indexer import index

from conftest import write_fits


class Clock:
    def __init__(self, start: datetime | None = None, *, catalog_time: bool = True):
        self.now = start or datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)
        if catalog_time:
            from altair.catalog import db

            db.use_clock(self)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_sync(catalog, config, fake_hub, clock=None) -> HubSync:
    return HubSync(catalog, config, fake_hub.client(), clock=clock or Clock())


def index_night(catalog, config, tmp_path, fake_hub, lights=3, *, object_="#34 M31", start=0, sub="night") -> None:
    """Write `lights` frames plus one flat and index them with the Hub config."""
    for i in range(start, start + lights):
        write_fits(tmp_path / sub / f"L_{i:04d}.fits", object_=object_, date_obs=f"2026-09-25T06:{i % 60:02d}:02", seed=i)
    write_fits(tmp_path / sub / f"F_{start:04d}.fits", imagetyp="FLAT", object_="FlatWizard", exposure=2.5, seed=10_000 + start)
    return index(catalog, config, tmp_path / sub, rig="esprit", hub_config=HubConfig.from_payload(fake_hub.config))


def pipeline_config(tmp_path, *, hub: bool = False, s3: dict | None = None, **overrides):
    """A config with a rig share, a NAS and (optionally) S3, all under tmp_path."""
    from altair.config import AltairConfig

    (tmp_path / "rig").mkdir(exist_ok=True)
    (tmp_path / "nas").mkdir(exist_ok=True)
    locations = [{"name": "nas", "kind": "fs", "root": str(tmp_path / "nas"), "min_free_percent": 0}]
    if s3 is not None:
        locations.append({"name": "s3", "kind": "s3", "bucket": "astro-archive", "region": "us-west-2", **s3})
    data = {
        "site": {"name": "Backyard", "latitude": 37.3, "longitude": -121.9, "timezone": "America/Los_Angeles"},
        "paths": {"state": str(tmp_path / "state"), "spool": str(tmp_path / "spool"), "work": str(tmp_path / "work")},
        # Space-pressure cleanup off: tmp file systems are often nearly full.
        "storage": {"cache": {"path": str(tmp_path / "cache")}, "locations": locations,
                    "cleanup": {"rig_defaults": {"target_free_percent": 0}, "nas": {"target_free_percent": 0}}},
        "rigs": {"esprit": {"raw_root": str(tmp_path / "rig"), "telescope": "esprit100", "camera": "asi2600mm", "focal_length_mm": 550,
                            "collect": {"stable_seconds": 60, "parallel_files": 2},
                            "rotator": {"present": True, "keywords": ["ROTATOR"], "units": "steps", "tolerance": 50},
                            **({"hub": {"telescope": "backyard-16in", "optical_train": "esprit100_2600mm"}} if hub else {})}},
        "aliases": {"telescope": {"Esprit 100ED|ESPRIT100": "esprit100"}, "camera": {"ZWO ASI2600MM Pro": "asi2600mm"},
                    "filter": {"^(Ha|H-alpha)$": "Ha", "^(OIII|O3)$": "OIII", "^(SII|S2)$": "SII"}},
        "cameras": {"asi2600mm": {"type": "mono", "cooled": True}},
        "hub": {"enabled": hub, "base_url": "https://hub.test", "node": "altair-proc-01"},
    }
    for key, value in overrides.items():
        data[key] = {**data.get(key, {}), **value} if isinstance(value, dict) else value
    return AltairConfig.model_validate(data)
