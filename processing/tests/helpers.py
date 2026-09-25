from __future__ import annotations

from datetime import datetime, timedelta, timezone

from altair.hub.config_sync import HubConfig
from altair.hub.sync import HubSync
from altair.index.indexer import index

from conftest import write_fits


class Clock:
    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)

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
