"""Pluggable calibration + stacking backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StackResult:
    stacked_path: Path
    preview_path: Path | None = None


class StackingBackend(ABC):
    @abstractmethod
    def calibrate_and_stack(
        self,
        light_frames_dir: Path,
        master_frames_dir: Path | None,
        output_dir: Path,
        output_name: str,
    ) -> StackResult:
        """Calibrates and stacks every frame in `light_frames_dir`.

        Implementations should raise `StackingError` on failure rather
        than returning a partial/invalid result.
        """


class StackingError(RuntimeError):
    pass
