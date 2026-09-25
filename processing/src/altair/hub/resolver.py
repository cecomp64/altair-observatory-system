"""Hub target resolution (SPEC §17.2). Lights only; first match wins:

1. header token  ``#<id> ...`` in OBJECT, target on this rig's train (or its telescope)
2. name + coordinates: OBJECT equals the target name or an alias, frame within
   ``max_offset_fov_fraction`` x FOV diagonal of the target
3. coordinates only: exactly one non-draft target on this train within that offset
4. otherwise unlinked
"""
from __future__ import annotations

import math
import re
from typing import NamedTuple

from altair.config import HubResolve
from altair.hub import names
from altair.hub.config_sync import HubConfig

TOKEN = re.compile(r"^#(\d+)(\s|$)")


class Resolution(NamedTuple):
    target_id: int | None
    source: str  # header_token / name / coords / unlinked


UNLINKED = Resolution(None, "unlinked")


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r = math.radians
    a = math.sin(r(dec2 - dec1) / 2) ** 2 + math.cos(r(dec1)) * math.cos(r(dec2)) * math.sin(r(ra2 - ra1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


def resolve(*, object_header: str | None, ra: float | None, dec: float | None, telescope: str, optical_train: str,
            config: HubConfig, rules: HubResolve) -> Resolution:
    candidates = [t for t in config.targets.values()
                  if t["telescope"] == telescope and t.get("optical_train") in (optical_train, None) and t["status"] != "draft"]
    header = (object_header or "").strip()

    if rules.by_header_token and (match := TOKEN.match(header)):
        target_id = int(match.group(1))
        if any(t["id"] == target_id for t in candidates):
            return Resolution(target_id, "header_token")

    diagonal = config.fov_diagonal_deg(telescope, optical_train)
    max_offset = rules.max_offset_fov_fraction * diagonal if diagonal else None

    def near(target: dict) -> bool:
        return (max_offset is not None and ra is not None and dec is not None
                and separation_deg(ra, dec, target["ra_deg"], target["dec_deg"]) <= max_offset)

    if rules.by_name and header:
        key = names.normalize(TOKEN.sub("", header).strip() or header)
        named = [t for t in candidates if key in {names.normalize(n) for n in [t["name"], *t.get("aliases", [])]}]
        close = [t for t in named if near(t)]
        if len(close) == 1:
            return Resolution(close[0]["id"], "name")

    if rules.by_coordinates:
        exact_train = [t for t in candidates if t.get("optical_train") == optical_train]
        close = [t for t in exact_train if near(t)]
        if len(close) == 1:
            return Resolution(close[0]["id"], "coords")

    return UNLINKED
