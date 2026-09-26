"""The phase-1 CLI end to end from altair.yaml: NAS init, collect, backup,
cleanup (dry run), ledger, catalog backup and restore."""
from __future__ import annotations

import yaml
from click.testing import CliRunner

from altair.cli import main

from conftest import write_fits
from helpers import pipeline_config


def test_phase_one_commands(tmp_path, s3_client, monkeypatch):
    config = pipeline_config(tmp_path, s3={}, rigs={"esprit": {**pipeline_config(tmp_path).rigs["esprit"].model_dump(mode="json"),
                                                               "collect": {"stable_seconds": 0}}})
    path = tmp_path / "altair.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    from altair import cli_context
    from altair.storage.locations import S3Location

    monkeypatch.setattr(cli_context.Ctx, "s3", lambda self, required=True: S3Location(self.config.storage.s3, s3_client))
    write_fits(tmp_path / "rig" / "2026-09-24" / "L_1.fits", seed=1)
    run = lambda *args: CliRunner().invoke(main, ["--config", str(path), *args], catch_exceptions=False)

    assert "NAS ready" in run("storage", "nas", "init").output
    assert run("storage", "nas", "status").exit_code == 0
    assert "collected 1" in run("collect", "now", "--rig", "esprit").output   # stable_seconds 0: collected on first sight
    assert "raw_light" in run("storage", "backup", "status").output
    assert "uploaded 1" in run("storage", "backup", "run").output
    assert "rig:esprit: reachable" in run("storage", "status").output
    assert "would delete" in run("storage", "cleanup", "--dry-run").output
    assert "(dry run)" in run("storage", "ledger").output
    assert "backed up to catalog/" in run("storage", "backup-catalog").output
    located = run("storage", "locate", "raw/esprit/2026-09-24/L_1.fits").output
    assert "nas: present" in located and "s3: present STANDARD_IA" in located
    assert "night 2026-09-24: open" in run("collect", "status").output
    assert "closed" in run("collect", "close-night", "--rig", "esprit", "--night", "2026-09-24").output
    assert "IAM" not in run("storage", "s3", "policy").output and '"s3:DeleteObject"' in run("storage", "s3", "policy").output
