"""Sanity checks on a night stack before it is published (SPEC §6.6 step 1).
Returns the problems found; an empty list means the master may be published."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from altair.config import AltairConfig
from altair.executor.contract import RunResult


def check_image(path: str | Path) -> str | None:
    """The file opens, holds finite data, and isn't blank."""
    from altair.hub.previews import load_image

    try:
        data = load_image(path)
    except Exception as exc:  # noqa: BLE001 - any read failure is the verdict
        return f"the master doesn't open ({exc})"
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return "the master has no finite pixels"
    if float(np.nanmax(finite)) == float(np.nanmin(finite)):
        return "the master is flat (all pixels equal)"
    return None


def night_stack(result: RunResult, plan: dict[str, Any], settings: dict[str, Any], config: AltairConfig,
                *, read_image: bool = True) -> list[str]:
    problems = []
    master = result.output("master")
    if master is None:
        return ["the runner reported no master"]
    if not Path(master.path).exists():
        return [f"the master {master.path} doesn't exist"]
    if read_image and (problem := check_image(master.path)):
        problems.append(problem)
    m = result.metrics
    n_lights = sum(len(g["lights"]) for g in plan["groups"])
    used = int(m.get("frames", n_lights))
    rejected = int(m.get("rejected", n_lights - used))
    min_lights = int(settings.get("min_lights_per_stack") or config.triggers.min_lights_per_stack)
    if used < min_lights:
        problems.append(f"{used} frame(s) used, fewer than min_lights_per_stack ({min_lights})")
    if n_lights and rejected / n_lights > config.night_processing.max_rejected_fraction:
        problems.append(f"{rejected} of {n_lights} frames rejected (more than {config.night_processing.max_rejected_fraction:.0%})")
    # Low overlap with the reference is not fatal here: the master is published
    # and the merge gate LOW_OVERLAP keeps it out of the merge (§9.4).
    return problems
