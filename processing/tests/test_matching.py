"""Table-driven tests for the calibration matching rules (SPEC §8, phase 2
exit criteria): rotator Δ at tolerance ±1 step, wrap-around, missing
positions, an equipment event between flat and light, darks, bias, dark-flats."""
from __future__ import annotations

import pytest

from altair.config import CalibrationMatching, Rotator
from altair.planner.matching import Match, Miss, match_bias, match_dark, match_darkflat, match_flat, match_flat_dark, rotator_buckets, rotator_delta

RULES = CalibrationMatching()
STEPS = Rotator(present=True, units="steps", steps_per_revolution=108000, tolerance=50)
LINEAR = Rotator(present=True, units="steps", tolerance=50)
DEGREES = Rotator(present=True, units="degrees", tolerance=1.0)
NONE = Rotator(present=False)


@pytest.mark.parametrize("a, b, rotator, expected", [
    (31250, 31250, STEPS, 0), (31250, 31300, STEPS, 50), (107990, 10, STEPS, 20),   # wrap-around
    (107990, 10, LINEAR, 107980), (359.5, 0.5, DEGREES, 1.0), (10, 350, DEGREES, 20), (90, 270, DEGREES, 180),
])
def test_rotator_delta(a, b, rotator, expected):
    assert rotator_delta(a, b, rotator) == pytest.approx(expected)


def test_rotator_buckets_group_positions_within_tolerance():
    assert rotator_buckets([100, 120, 149, 200, None, 107990, 20], STEPS) == [100, 100, 100, 200, None, 107990, 107990]


def flat(**kw):
    base = {"kind": "FLAT", "rig": "esprit", "filter": "SII", "binning": "1x1", "width": 6248, "height": 4176, "focal_length": 550,
            "rotator_pos": 31250, "night": "2026-09-24", "taken_at": "2026-09-25T12:00:00Z", "n_frames": 30, "sha256": "f1"}
    return {**base, **kw}


NEED = {"rig": "esprit", "filter": "SII", "binning": "1x1", "width": 6248, "height": 4176, "focal_length": 550, "rotator_pos": 31250,
        "night": "2026-09-24", "taken_at": "2026-09-25T08:00:00Z"}


def flat_match(masters, need=NEED, rotator=STEPS, events=()):
    return match_flat(need, masters, RULES, rotator, focal_tolerance=10, events=list(events))


@pytest.mark.parametrize("pos, ok", [(31250 + 50, True), (31250 - 50, True), (31250 + 51, False), (31250 - 51, False)])
def test_flat_rotator_at_tolerance_plus_minus_one_step(pos, ok):
    result = flat_match([flat(rotator_pos=pos)])
    assert isinstance(result, Match) is ok
    if not ok:
        assert result.kind == "FLAT_MISSING" and "rotator Δ 51" in result.reason and result.nearest["sha256"] == "f1"


def test_flat_matches_across_the_wrap():
    need = {**NEED, "rotator_pos": 10}
    assert isinstance(flat_match([flat(rotator_pos=107980)], need), Match)
    assert isinstance(flat_match([flat(rotator_pos=107980)], need, rotator=LINEAR), Miss)


def test_missing_rotator_positions_never_match():
    assert flat_match([flat()], {**NEED, "rotator_pos": None}).kind == "ROTATOR_POSITION_UNKNOWN"
    assert "no rotator position" in flat_match([flat(rotator_pos=None)]).reason
    assert isinstance(flat_match([flat(rotator_pos=None)], {**NEED, "rotator_pos": None}, rotator=NONE), Match)   # no rotator on the rig


def test_an_equipment_event_between_flat_and_light_splits_validity():
    event = {"rig": "esprit", "at": "2026-09-25T10:00:00Z", "kind": "sensor_cleaned", "filter": None}
    assert "sensor_cleaned" in flat_match([flat()], events=[event]).reason
    other_filter = {**event, "filter": "Ha"}
    assert isinstance(flat_match([flat()], events=[other_filter]), Match)
    before_both = {**event, "at": "2026-09-20T00:00:00Z"}
    assert isinstance(flat_match([flat()], events=[before_both]), Match)


def test_flat_preference_same_night_then_after_then_before():
    same = flat(sha256="same")
    after = flat(sha256="after", night="2026-09-26")
    before = flat(sha256="before", night="2026-09-20")
    assert flat_match([before, after, same]).master["sha256"] == "same"
    assert flat_match([before, after]).master["sha256"] == "after"
    assert flat_match([before]).master["sha256"] == "before"
    assert isinstance(flat_match([flat(night="2026-06-01")]), Miss)   # beyond max_age_days (60)


@pytest.mark.parametrize("change, ok", [({"filter": "Ha"}, False), ({"binning": "2x2"}, False), ({"width": 3124}, False),
                                        ({"focal_length": 561}, False), ({"focal_length": 559}, True), ({"rig": "rasa"}, False)])
def test_flat_exact_keys_and_focal_length_tolerance(change, ok):
    assert isinstance(flat_match([flat(**change)]), Match) is ok


def dark(**kw):
    base = {"kind": "DARK", "camera": "asi2600mm", "gain": 100, "offset": 50, "binning": "1x1", "readout_mode": None, "width": 6248,
            "height": 4176, "exposure": 300.0, "sensor_temp": -10.0, "night": "2026-09-01", "n_frames": 40, "sha256": "d1"}
    return {**base, **kw}


DARK_NEED = {"camera": "asi2600mm", "gain": 100, "offset": 50, "binning": "1x1", "readout_mode": None, "width": 6248, "height": 4176,
             "exposure": 300.0, "sensor_temp": -10.2, "night": "2026-09-24"}


def test_dark_matching():
    assert isinstance(match_dark(DARK_NEED, [dark()], RULES), Match)
    assert match_dark(DARK_NEED, [dark(exposure=180.0)], RULES).kind == "DARK_MISSING"
    assert isinstance(match_dark(DARK_NEED, [dark(sensor_temp=-12.3)], RULES), Miss)          # 2.1 °C: beyond 2 °C
    assert isinstance(match_dark(DARK_NEED, [dark(gain=0)], RULES), Miss)
    assert isinstance(match_dark(DARK_NEED, [dark(night="2026-01-01")], RULES), Miss)          # older than 180 days
    closest = match_dark(DARK_NEED, [dark(sha256="warm", sensor_temp=-8.5), dark(sha256="cold", sensor_temp=-10.0)], RULES)
    assert closest.master["sha256"] == "cold"
    recent = match_dark(DARK_NEED, [dark(sha256="old"), dark(sha256="new", night="2026-09-20")], RULES)
    assert recent.master["sha256"] == "new"


def test_bias_and_darkflat_by_strategy():
    need = {**DARK_NEED, "exposure": 2.0}
    darkflat = {**dark(kind="DARKFLAT", exposure=2.05, sha256="df")}
    bias = {**dark(kind="BIAS", exposure=0, sha256="b")}
    assert match_darkflat(need, [darkflat], RULES).master["sha256"] == "df"
    assert match_darkflat(need, [{**darkflat, "exposure": 2.2}], RULES).kind == "DARKFLAT_MISSING"
    assert match_bias(need, [bias], RULES).master["sha256"] == "b"
    assert match_flat_dark(need, [darkflat, bias], RULES).master["sha256"] == "df"                       # darkflat preferred
    assert match_flat_dark(need, [bias], RULES).master["sha256"] == "b"                                   # falls back to bias
    only = CalibrationMatching(flat_dark_strategy="darkflat_only")
    assert match_flat_dark(need, [bias], only).kind == "DARKFLAT_MISSING"
    bias_only = CalibrationMatching(flat_dark_strategy="bias_only")
    assert match_flat_dark(need, [darkflat, bias], bias_only).master["sha256"] == "b"
