"""Night weights for a merge (SPEC §9.5 step 3).

The MERGE job's measure phase measures every night master in one PixInsight
pass, so the values share one scale. From those measurements:

- ``measured_psf_signal`` (default): each master's PSF Signal Weight.
- ``inverse_noise_variance``: ``(k / σ)²`` with σ the multiscale-median noise
  of the normalized master and k its photometric scale to the reference.
- ``frame_weight_sum``: the sum of the raw PSF Signal Weights of the frames
  the night accepted, from Altair's own per-frame measurements (WBPP's
  WBPPWGHT is normalized per run and not comparable across nights).
"""
from __future__ import annotations

from typing import Any


class WeightError(Exception):
    pass


def compute(mode: str, nights: list[dict[str, Any]], measurements: list[dict[str, Any]]) -> dict[str, float]:
    """Weight per night master SHA-256; every weight is > 0."""
    by_sha = {m["sha256"]: m for m in measurements if m.get("sha256")}
    out: dict[str, float] = {}
    for night in nights:
        sha = night["sha256"]
        if mode == "frame_weight_sum":
            w = night.get("frame_weight_sum")
        else:
            m = by_sha.get(sha)
            if m is None:
                raise WeightError(f"night {night['night']} was not measured")
            if mode == "measured_psf_signal":
                w = m.get("psf_signal_weight")
            elif mode == "inverse_noise_variance":
                sigma = m.get("noise_sigma")
                w = (float(m.get("scale") or 1.0) / sigma) ** 2 if sigma else None
            else:
                raise WeightError(f"unknown night_weighting {mode!r}")
        if w is None or not w > 0:
            raise WeightError(f"night {night['night']} has no usable {mode} weight ({w})")
        out[sha] = float(w)
    return out


def percentages(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    return {k: 100.0 * v / total for k, v in weights.items()} if total else {}


def normalization_reference(weights: dict[str, float]) -> str:
    """The master with the highest weight is LocalNormalization's reference (§9.5 step 2)."""
    return max(sorted(weights), key=lambda k: weights[k])
