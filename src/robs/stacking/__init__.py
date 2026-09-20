from __future__ import annotations

from ..config import StackingConfig
from .base import StackingBackend, StackingError, StackResult
from .pixinsight import PixInsightStackingBackend
from .siril import SirilStackingBackend

__all__ = ["StackingBackend", "StackingError", "StackResult", "build_backend"]


def build_backend(config: StackingConfig) -> StackingBackend:
    if config.backend == "siril":
        return SirilStackingBackend(executable_path=config.executable_path or "siril-cli")
    if config.backend == "pixinsight":
        return PixInsightStackingBackend(executable_path=config.executable_path or "PixInsight")
    raise ValueError(f"Unknown stacking backend: {config.backend!r}")
