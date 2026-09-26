"""Nights that close without a session-end signal (SPEC §6.1): quiescence
after dawn, and the daily scheduled fallback."""
from __future__ import annotations

from datetime import datetime, timezone

from altair import astro
from altair.catalog.db import Catalog
from altair.triggers import Triggers

from helpers import Clock, pipeline_config


def open_night(catalog, rig="esprit", night="2026-09-24", last_frame="2026-09-25T11:00:00Z"):
    catalog.execute("INSERT INTO collections(rig, night, state, opened_at, last_frame_at) VALUES (?, ?, 'open', ?, ?)",
                    (rig, night, last_frame, last_frame))


def test_dawn_in_san_jose_late_september():
    dawn = astro.dawn("2026-09-24", "America/Los_Angeles", 37.3, -121.9)
    assert datetime(2026, 9, 25, 13, 30, tzinfo=timezone.utc) < dawn < datetime(2026, 9, 25, 13, 50, tzinfo=timezone.utc)  # ~06:40 PDT


def test_quiescence_closes_a_night_only_after_dawn(tmp_path):
    config = pipeline_config(tmp_path, rigs={"esprit": {**pipeline_config(tmp_path).rigs["esprit"].model_dump(mode="json"), "raw_root": None}})
    catalog = Catalog(config.catalog_path)
    clock = Clock(datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))   # 05:00 PDT: quiet for an hour, still dark
    open_night(catalog)
    triggers = Triggers(catalog, config, clock=clock)
    assert triggers.tick() == []
    clock.now = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)        # after dawn
    assert triggers.tick() == [("esprit", "2026-09-24", "quiescence")]
    assert catalog.one("SELECT state, closed_by FROM collections")["closed_by"] == "quiescence"
    assert catalog.one("SELECT severity FROM issues WHERE kind = 'SESSION_END_MARKER_MISSING'")["severity"] == "warning"

    open_night(catalog, night="2026-09-25", last_frame="2026-09-26T11:00:00Z")
    catalog.execute("UPDATE collections SET state = 'closed', closed_by = 'session_end' WHERE night = '2026-09-25'")
    triggers.tick()
    assert catalog.one("SELECT status FROM issues WHERE kind = 'SESSION_END_MARKER_MISSING'")["status"] == "resolved"


def test_an_unreachable_rig_is_never_quiet_enough_to_close(tmp_path):
    config = pipeline_config(tmp_path, rigs={"esprit": {**pipeline_config(tmp_path).rigs["esprit"].model_dump(mode="json"), "raw_root": None}})
    catalog = Catalog(config.catalog_path)
    open_night(catalog)
    catalog.execute("INSERT INTO locations(name, kind, durable, reachable) VALUES ('rig:esprit', 'fs', 0, 0)")
    assert Triggers(catalog, config, clock=Clock(datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc))).tick() == []


def test_scheduled_fallback_closes_old_open_nights_once_a_day_unless_the_rig_is_down(tmp_path):
    base = pipeline_config(tmp_path).rigs["esprit"].model_dump(mode="json")
    config = pipeline_config(tmp_path, rigs={"esprit": {**base, "raw_root": None}, "rasa": {**base, "raw_root": None, "telescope": "rasa8"}},
                             triggers={"require_after_dawn": True, "scheduled_fallback_local": "09:00"})
    catalog = Catalog(config.catalog_path)
    clock = Clock(datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc))   # 08:00 PDT
    open_night(catalog, last_frame="2026-09-25T14:59:00Z")             # still busy: no quiescence
    open_night(catalog, rig="rasa", last_frame="2026-09-25T14:59:00Z")
    catalog.execute("INSERT INTO locations(name, kind, durable, reachable) VALUES ('rig:rasa', 'fs', 0, 0)")
    triggers = Triggers(catalog, config, clock=clock)
    assert triggers.tick() == []
    clock.now = datetime(2026, 9, 25, 16, 1, tzinfo=timezone.utc)      # 09:01 PDT, frames still arriving
    catalog.execute("UPDATE collections SET last_frame_at = '2026-09-25T16:00:00Z'")
    assert triggers.tick() == [("esprit", "2026-09-24", "scheduled")]
    assert catalog.one("SELECT state FROM collections WHERE rig = 'rasa'")["state"] == "open"
    catalog.execute("UPDATE locations SET reachable = 1 WHERE name = 'rig:rasa'")
    assert triggers.tick() == []                                        # once a day
