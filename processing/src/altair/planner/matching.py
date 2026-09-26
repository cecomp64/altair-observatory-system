"""Calibration matching (SPEC §8). Pure functions over plain dicts, so every
rule is table-testable.

A *candidate* is a calibration master: a ``calibration_masters`` row, or a
master a CALIB_MASTER job of the same plan is about to build (it carries
``pending_job``). A *need* describes what a light group (or a flat group,
for its dark-flat/bias) requires. The result is a :class:`Match` with the
chosen master and the evidence, or a :class:`Miss` with the nearest
candidate and why it doesn't fit, which becomes the issue's text.

The system never falls back beyond the configured tolerances (§8.4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from altair.config import CalibrationMatching, Rotator


@dataclass
class Match:
    master: dict[str, Any]
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Miss:
    kind: str                         # FLAT_MISSING / DARK_MISSING / BIAS_MISSING / DARKFLAT_MISSING / ROTATOR_POSITION_UNKNOWN
    reason: str
    nearest: dict[str, Any] | None = None


# ── rotator (§8.2) ───────────────────────────────────────────────────────
def rotator_delta(a: float, b: float, rotator: Rotator) -> float:
    """Distance between two mechanical positions in the rig's units:
    circular for degrees, and for steps when steps per revolution is known."""
    diff = abs(a - b)
    if rotator.units == "degrees":
        diff %= 360
        return min(diff, 360 - diff)
    if rotator.steps_per_revolution:
        diff %= rotator.steps_per_revolution
        return min(diff, rotator.steps_per_revolution - diff)
    return diff


def rotator_buckets(positions: list[float | None], rotator: Rotator) -> list[float | None]:
    """A representative position per frame: frames within ``tolerance`` of a
    bucket's first position share it (used to group lights and flats)."""
    anchors: list[float] = []
    out: list[float | None] = []
    for pos in positions:
        if pos is None:
            out.append(None)
            continue
        anchor = next((a for a in anchors if rotator_delta(a, pos, rotator) <= rotator.tolerance), None)
        if anchor is None:
            anchors.append(pos)
            anchor = pos
        out.append(anchor)
    return out


# ── helpers ──────────────────────────────────────────────────────────────
def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _age_days(master_night: str, light_night: str) -> int:
    return (_day(light_night) - _day(master_night)).days


def _same(a: Any, b: Any) -> bool:
    return (a is None and b is None) or a == b


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def event_between(events: list[dict], rig: str, filter_: str | None, t1: str | None, t2: str | None) -> dict | None:
    """An equipment event on ``rig`` (for ``filter_`` or all filters) strictly
    between two times splits flat validity (§8.3)."""
    a, b = _ts(t1), _ts(t2)
    if a is None or b is None:
        return None
    lo, hi = min(a, b), max(a, b)
    for event in events:
        if event["rig"] != rig or (event.get("filter") not in (None, filter_)):
            continue
        at = _ts(event["at"])
        if at and lo < at < hi:
            return event
    return None


# ── darks, bias, dark-flats (§8.1) ───────────────────────────────────────
def _sensor_key_matches(need: dict, master: dict) -> bool:
    return (master["camera"] == need["camera"] and _same(master.get("gain"), need.get("gain")) and _same(master.get("offset"), need.get("offset"))
            and master.get("binning") == need.get("binning") and _same(master.get("readout_mode"), need.get("readout_mode")))


def match_dark(need: dict, masters: list[dict], rules: CalibrationMatching, *, cooled: bool = True) -> Match | Miss:
    """camera, gain, offset, binning, readout, size exact; exposure and sensor
    temperature within tolerance; closest temp → most recent → most frames."""
    r = rules.dark
    pool = [m for m in masters if m["kind"] == "DARK" and _sensor_key_matches(need, m)
            and (m.get("width"), m.get("height")) == (need.get("width"), need.get("height"))]
    nearest = min(pool, key=lambda m: abs((m.get("exposure") or 0) - (need.get("exposure") or 0)), default=None)
    ok = []
    for m in pool:
        if abs((m.get("exposure") or 0) - (need.get("exposure") or 0)) > r.exposure_tolerance_s + 1e-9:
            continue
        if cooled and need.get("sensor_temp") is not None:
            if m.get("sensor_temp") is None or abs(m["sensor_temp"] - need["sensor_temp"]) > r.temp_tolerance_c:
                continue
        if abs(_age_days(m["night"], need["night"])) > r.max_age_days:
            continue
        ok.append(m)
    if not ok:
        return Miss("DARK_MISSING", _dark_reason(need, nearest), nearest)
    best = min(ok, key=lambda m: (abs((m.get("sensor_temp") or 0) - (need.get("sensor_temp") or 0)) if cooled else 0,
                                  -_day(m["night"]).toordinal(), -(m.get("n_frames") or 0)))
    return Match(best, {"exposure": best.get("exposure"), "temp_delta": _delta(best.get("sensor_temp"), need.get("sensor_temp"))})


def _dark_reason(need: dict, nearest: dict | None) -> str:
    base = (f"no master dark for {need['camera']} gain {need.get('gain')} offset {need.get('offset')} bin {need.get('binning')} "
            f"{need.get('exposure'):g}s at {need.get('sensor_temp')}°C")
    if nearest:
        base += f"; nearest is {nearest.get('exposure'):g}s at {nearest.get('sensor_temp')}°C from {nearest['night']}"
    return base


def match_bias(need: dict, masters: list[dict], rules: CalibrationMatching) -> Match | Miss:
    pool = [m for m in masters if m["kind"] == "BIAS" and _sensor_key_matches(need, m)
            and abs(_age_days(m["night"], need["night"])) <= rules.bias.max_age_days]
    if not pool:
        return Miss("BIAS_MISSING", f"no master bias for {need['camera']} gain {need.get('gain')} offset {need.get('offset')} bin {need.get('binning')}")
    best = max(pool, key=lambda m: (_day(m["night"]).toordinal(), m.get("n_frames") or 0))
    return Match(best, {})


def match_darkflat(need: dict, masters: list[dict], rules: CalibrationMatching) -> Match | Miss:
    """For a flat group: exposure ≈ the flats' exposure; closest exposure → most recent."""
    r = rules.darkflat
    pool = [m for m in masters if m["kind"] == "DARKFLAT" and _sensor_key_matches(need, m)
            and abs((m.get("exposure") or 0) - (need.get("exposure") or 0)) <= r.exposure_tolerance_s + 1e-9
            and abs(_age_days(m["night"], need["night"])) <= r.max_age_days]
    if not pool:
        return Miss("DARKFLAT_MISSING", f"no master dark-flat for {need['camera']} bin {need.get('binning')} {need.get('exposure'):g}s")
    best = min(pool, key=lambda m: (abs((m.get("exposure") or 0) - (need.get("exposure") or 0)), -_day(m["night"]).toordinal()))
    return Match(best, {"exposure": best.get("exposure")})


def match_flat_dark(need: dict, masters: list[dict], rules: CalibrationMatching) -> Match | Miss:
    """What a flat master is calibrated with, by ``flat_dark_strategy``."""
    if rules.flat_dark_strategy == "bias_only":
        return match_bias(need, masters, rules)
    darkflat = match_darkflat(need, masters, rules)
    if isinstance(darkflat, Match) or rules.flat_dark_strategy == "darkflat_only":
        return darkflat
    bias = match_bias(need, masters, rules)
    return bias if isinstance(bias, Match) else darkflat


# ── flats (§8.1–§8.3) ────────────────────────────────────────────────────
def match_flat(need: dict, masters: list[dict], rules: CalibrationMatching, rotator: Rotator, *, focal_tolerance: float,
               events: list[dict]) -> Match | Miss:
    """rig, filter, binning, size exact; focal length, rotator position and
    age within tolerance; never across an equipment event; preference order
    same night → nearest after → nearest before."""
    r = rules.flat
    need_rot = need.get("rotator_pos")
    if rotator.present and need_rot is None:
        return Miss("ROTATOR_POSITION_UNKNOWN", "the lights have no rotator position, so no flat can be proven to match")
    pool = [m for m in masters if m["kind"] == "FLAT" and m.get("rig") == need["rig"] and m.get("filter") == need["filter"]
            and m.get("binning") == need.get("binning") and (m.get("width"), m.get("height")) == (need.get("width"), need.get("height"))]
    reasons: list[tuple[dict, str]] = []
    ok: list[dict] = []
    for m in pool:
        if need.get("focal_length") is not None and m.get("focal_length") is not None and abs(m["focal_length"] - need["focal_length"]) > focal_tolerance:
            reasons.append((m, f"focal length {m['focal_length']:g} vs {need['focal_length']:g} mm"))
            continue
        if rotator.present:
            if m.get("rotator_pos") is None:
                reasons.append((m, "the flat has no rotator position"))
                continue
            delta = rotator_delta(m["rotator_pos"], need_rot, rotator)
            if delta > rotator.tolerance:
                reasons.append((m, f"rotator Δ {delta:g} > {rotator.tolerance:g} {rotator.units}"))
                continue
        age = _age_days(m["night"], need["night"])
        if abs(age) > r.max_age_days:
            reasons.append((m, f"{abs(age)} days apart (max {r.max_age_days:g})"))
            continue
        event = event_between(events, need["rig"], need["filter"], m.get("taken_at"), need.get("taken_at"))
        if event:
            reasons.append((m, f"{event['kind']} on {str(event['at'])[:10]} between the flat and the lights"))
            continue
        ok.append(m)
    if ok:
        best = min(ok, key=lambda m: _flat_preference(m, need, r.prefer))
        return Match(best, {"night": best["night"], "rotator_pos": best.get("rotator_pos"),
                            "rotator_delta": rotator_delta(best["rotator_pos"], need_rot, rotator) if rotator.present else None,
                            "age_days": _age_days(best["night"], need["night"])})
    if not pool:
        return Miss("FLAT_MISSING", f"no master flat for filter {need['filter']} bin {need.get('binning')} on {need['rig']}")
    nearest, why = min(reasons, key=lambda mr: abs(_age_days(mr[0]["night"], need["night"])))
    return Miss("FLAT_MISSING", f"nearest candidate is {nearest['night']} {nearest.get('filter')}"
                                + (f" @ {nearest.get('rotator_pos'):g} {rotator.units}" if nearest.get("rotator_pos") is not None else "")
                                + f" ({why})", nearest)


def _flat_preference(m: dict, need: dict, prefer: list[str]) -> tuple:
    age = _age_days(m["night"], need["night"])          # > 0: the flat is from before the lights
    if age == 0:
        category = "same_night"
    elif age < 0:
        category = "nearest_after"
    else:
        category = "nearest_before"
    rank = prefer.index(category) if category in prefer else len(prefer)
    return (rank, abs(age), -(m.get("n_frames") or 0))


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 3)
