from __future__ import annotations

from unittest.mock import patch

import pytest

from robs.stacking import build_backend
from robs.config import StackingConfig
from robs.stacking.base import StackingError
from robs.stacking.pixinsight import PixInsightStackingBackend
from robs.stacking.siril import SirilStackingBackend


def test_build_backend_selects_siril():
    backend = build_backend(StackingConfig(backend="siril", executable_path="siril-cli"))
    assert isinstance(backend, SirilStackingBackend)


def test_build_backend_selects_pixinsight():
    backend = build_backend(StackingConfig(backend="pixinsight"))
    assert isinstance(backend, PixInsightStackingBackend)


def test_build_backend_rejects_unknown_backend():
    with pytest.raises(ValueError):
        build_backend(StackingConfig(backend="nonsense"))


def test_pixinsight_requires_a_pjsr_script(tmp_path):
    backend = PixInsightStackingBackend()

    with pytest.raises(StackingError, match="pjsr_script_path"):
        backend.calibrate_and_stack(tmp_path, None, tmp_path / "out", "stack")


def test_siril_raises_when_executable_missing(tmp_path):
    backend = SirilStackingBackend(executable_path="/does/not/exist/siril-cli")
    light_dir = tmp_path / "lights"
    light_dir.mkdir()

    with pytest.raises(StackingError, match="not found"):
        backend.calibrate_and_stack(light_dir, None, tmp_path / "out", "stack")


def test_siril_raises_when_output_not_produced(tmp_path):
    light_dir = tmp_path / "lights"
    light_dir.mkdir()
    backend = SirilStackingBackend(executable_path="/bin/true")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        with pytest.raises(StackingError, match="was not produced"):
            backend.calibrate_and_stack(light_dir, None, tmp_path / "out", "stack")
