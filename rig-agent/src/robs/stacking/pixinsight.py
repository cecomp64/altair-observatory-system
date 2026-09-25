"""PixInsight calibration/stacking backend.

PixInsight is driven via PJSR (PixInsight JavaScript Runtime) scripts
run headless: `PixInsight -x=/path/to/script.js`. Unlike Siril, there's
no single built-in "stack these lights" console command — a real
deployment needs a WBPP (WeightedBatchPreprocessing) process icon or a
custom PJSR script exported from the target install, which is
site-specific.

This class defines the same interface as `SirilStackingBackend` so it
can be swapped in via config (`stacking.backend: pixinsight`), and
shells out to a *site-provided* PJSR script rather than trying to
generate one — provide `pjsr_script_path` and this backend will
invoke it with the lights/masters/output directories as arguments.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .base import StackingBackend, StackingError, StackResult

logger = logging.getLogger(__name__)


class PixInsightStackingBackend(StackingBackend):
    def __init__(self, executable_path: str = "PixInsight", pjsr_script_path: str | None = None):
        self.executable_path = executable_path
        self.pjsr_script_path = pjsr_script_path

    def calibrate_and_stack(
        self,
        light_frames_dir: Path,
        master_frames_dir: Path | None,
        output_dir: Path,
        output_name: str,
    ) -> StackResult:
        if not self.pjsr_script_path:
            raise StackingError(
                "PixInsight backend requires 'stacking.pjsr_script_path' pointing at a "
                "site-provided WBPP/stacking PJSR script — see stacking/pixinsight.py."
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        args = [
            self.executable_path,
            f"-x={self.pjsr_script_path}",
            f"--lights={light_frames_dir}",
            f"--masters={master_frames_dir or ''}",
            f"--output={output_dir}",
            f"--name={output_name}",
        ]

        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=60 * 60)
        except FileNotFoundError as e:
            raise StackingError(f"PixInsight executable not found: {self.executable_path}") from e

        if result.returncode != 0:
            raise StackingError(f"PixInsight exited {result.returncode}: {result.stderr or result.stdout}")

        stacked_path = output_dir / f"{output_name}.xisf"
        if not stacked_path.exists():
            raise StackingError(f"PixInsight reported success but {stacked_path} was not produced")

        preview_path = output_dir / f"{output_name}_preview.jpg"
        return StackResult(stacked_path=stacked_path, preview_path=preview_path if preview_path.exists() else None)
