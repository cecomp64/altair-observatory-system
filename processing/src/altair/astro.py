"""Just enough solar position for "after dawn" (SPEC §6.1 quiescence and
the processing window): a low-precision Sun, good to a few arcminutes."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

CIVIL = 6.0


def sun_altitude(at: datetime, latitude: float, longitude: float) -> float:
    d = (at.astimezone(timezone.utc) - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    ecliptic_lon = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    obliquity = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(obliquity) * math.sin(ecliptic_lon), math.cos(ecliptic_lon))
    dec = math.asin(math.sin(obliquity) * math.sin(ecliptic_lon))
    gmst_hours = (18.697374558 + 24.06570982441908 * d) % 24
    hour_angle = math.radians(gmst_hours * 15 + longitude) - ra
    lat = math.radians(latitude)
    return math.degrees(math.asin(math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(hour_angle)))


def dawn(night: date | str, tz: str, latitude: float, longitude: float, depression: float = CIVIL) -> datetime:
    """When the Sun rises above -``depression``° on the morning after
    ``night`` (the local date the night started). Falls back to local noon
    in polar summer or winter."""
    night = date.fromisoformat(night) if isinstance(night, str) else night
    zone = ZoneInfo(tz)
    start = datetime.combine(night + timedelta(days=1), time(0, 0), zone)
    noon = start + timedelta(hours=12)
    step = timedelta(minutes=5)
    t = start
    if sun_altitude(t, latitude, longitude) >= -depression:
        return t.astimezone(timezone.utc)
    while t < noon:
        nxt = t + step
        if sun_altitude(nxt, latitude, longitude) >= -depression:
            lo, hi = t, nxt
            while hi - lo > timedelta(seconds=20):
                mid = lo + (hi - lo) / 2
                lo, hi = (mid, hi) if sun_altitude(mid, latitude, longitude) < -depression else (lo, mid)
            return hi.astimezone(timezone.utc)
        t = nxt
    return noon.astimezone(timezone.utc)


def after_dawn(now: datetime, night: date | str, tz: str, latitude: float, longitude: float) -> bool:
    return now >= dawn(night, tz, latitude, longitude)
