"""altair doctor against a fake Hub, including the api_revision check."""
from __future__ import annotations

import yaml
from click.testing import CliRunner
from observatory_contracts import API_REVISION

from altair import cli


def run_doctor(tmp_path, monkeypatch, config, fake_hub):
    path = tmp_path / "altair.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    monkeypatch.setattr(cli.Ctx, "client", lambda self: fake_hub.client())
    return CliRunner().invoke(cli.main, ["--config", str(path), "doctor"])


def test_doctor_passes_against_a_matching_hub(tmp_path, monkeypatch, config, fake_hub):
    result = run_doctor(tmp_path, monkeypatch, config, fake_hub)

    assert f"[ok] Hub API revision compatible: api_revision {API_REVISION}" in result.output
    assert result.exit_code == 0, result.output


def test_doctor_fails_against_an_older_hub(tmp_path, monkeypatch, config, fake_hub):
    fake_hub.config["api_revision"] = API_REVISION - 1

    result = run_doctor(tmp_path, monkeypatch, config, fake_hub)

    assert "[FAIL] Hub API revision compatible" in result.output and "upgrade the Hub" in result.output
    assert result.exit_code == 1
