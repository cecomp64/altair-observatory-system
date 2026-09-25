"""Calibrates and stacks a night's lights using Siril's CLI scripting.

Requires `siril-cli` (or `siril -s` on some platforms). Master
calibration frames (bias/dark/flat) are expected as
`master_frames_dir/master_bias.fit`, `master_dark.fit`,
`master_flat.fit` — any that are missing are simply skipped in the
generated script.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from .base import StackingBackend, StackingError, StackResult

logger = logging.getLogger(__name__)


class SirilStackingBackend(StackingBackend):
    def __init__(self, executable_path: str = "siril-cli"):
        self.executable_path = executable_path

    def calibrate_and_stack(
        self,
        light_frames_dir: Path,
        master_frames_dir: Path | None,
        output_dir: Path,
        output_name: str,
    ) -> StackResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        script = self._build_script(light_frames_dir, master_frames_dir, output_dir, output_name)

        with tempfile.NamedTemporaryFile("w", suffix=".ssf", delete=False) as script_file:
            script_file.write(script)
            script_path = Path(script_file.name)

        try:
            result = subprocess.run(
                [self.executable_path, "-s", str(script_path)],
                capture_output=True,
                text=True,
                timeout=60 * 60,
            )
        except FileNotFoundError as e:
            raise StackingError(f"Siril executable not found: {self.executable_path}") from e
        finally:
            script_path.unlink(missing_ok=True)

        if result.returncode != 0:
            raise StackingError(f"Siril exited {result.returncode}: {result.stderr or result.stdout}")

        stacked_path = output_dir / f"{output_name}.fit"
        if not stacked_path.exists():
            raise StackingError(f"Siril reported success but {stacked_path} was not produced")

        preview_path = output_dir / f"{output_name}_preview.jpg"
        return StackResult(stacked_path=stacked_path, preview_path=preview_path if preview_path.exists() else None)

    def _build_script(
        self, light_frames_dir: Path, master_frames_dir: Path | None, output_dir: Path, output_name: str
    ) -> str:
        lines = [
            f'cd "{light_frames_dir}"',
            "convert lights -out=process",
            "cd process",
        ]

        master_bias = master_frames_dir / "master_bias.fit" if master_frames_dir else None
        master_dark = master_frames_dir / "master_dark.fit" if master_frames_dir else None
        master_flat = master_frames_dir / "master_flat.fit" if master_frames_dir else None

        calibrate_args = []
        if master_dark and master_dark.exists():
            calibrate_args.append(f'-dark="{master_dark}"')
        if master_flat and master_flat.exists():
            calibrate_args.append(f'-flat="{master_flat}"')
        if master_bias and master_bias.exists():
            calibrate_args.append(f'-bias="{master_bias}"')

        if calibrate_args:
            lines.append("calibrate lights " + " ".join(calibrate_args) + " -cfa -equalize_cfa -debayer")
            lines.append("register pp_lights")
            lines.append(f'stack r_pp_lights rej 3 3 -norm=addscale -output_norm -out="{output_dir / output_name}"')
        else:
            # No calibration frames available — register and stack raw lights.
            lines.append("register lights")
            lines.append(f'stack r_lights rej 3 3 -norm=addscale -output_norm -out="{output_dir / output_name}"')

        lines.append(f'load "{output_dir / output_name}"')
        lines.append("autostretch")
        lines.append(f'savejpg "{output_dir / (output_name + "_preview")}"')
        lines.append("close")

        return "\n".join(lines) + "\n"
