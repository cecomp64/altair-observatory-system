from __future__ import annotations

import pytest

from robs.config import ConfigError, TelescopeConfig


def test_loads_a_valid_config(telescope_config_yaml):
    config = TelescopeConfig.load(telescope_config_yaml)

    assert config.slug == "test-scope"
    assert config.api_base_url == "https://example.test"
    assert config.api_key == "test-token"


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        TelescopeConfig.load(tmp_path / "does-not-exist.yml")


def test_missing_required_field_raises_config_error(tmp_path):
    path = tmp_path / "bad.yml"
    path.write_text("slug: no-api-key\n")

    with pytest.raises(ConfigError):
        TelescopeConfig.load(path)


def test_env_override_takes_precedence(telescope_config_yaml, monkeypatch):
    monkeypatch.setenv("ROBS_TEST_SCOPE_API_KEY", "from-env")

    config = TelescopeConfig.load(telescope_config_yaml)

    assert config.api_key == "from-env"


def test_obsolete_legacy_settings_are_ignored(telescope_config_yaml, caplog):
    telescope_config_yaml.write_text(telescope_config_yaml.read_text() + 'data_pipeline: legacy\ns3_bucket: "old"\n')

    config = TelescopeConfig.load(telescope_config_yaml)

    assert not hasattr(config, "s3_bucket")
    assert "'data_pipeline' is no longer used" in caplog.text
