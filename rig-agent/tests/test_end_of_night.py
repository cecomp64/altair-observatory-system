from __future__ import annotations

from unittest.mock import MagicMock

import responses

from robs.api_client import ObservatoryApiClient
from robs.config import TelescopeConfig
from robs.end_of_night import publish_night
from robs.s3_publisher import S3Publisher


def _make_publisher(config: TelescopeConfig) -> S3Publisher:
    fake_boto_client = MagicMock()
    return S3Publisher(bucket=config.s3_bucket, prefix=config.s3_prefix, client=fake_boto_client)


@responses.activate
def test_publish_night_uploads_subs_and_reports_files(telescope_config_yaml):
    config = TelescopeConfig.load(telescope_config_yaml)
    api = ObservatoryApiClient(config.api_base_url, config.api_key)

    target_dir = config.subs_dir / "#5 M42"
    target_dir.mkdir()
    (target_dir / "LIGHT_Luminance_0001.fits").write_bytes(b"fake fits data")
    (target_dir / "LIGHT_Luminance_0002.fits").write_bytes(b"fake fits data")

    files_mock = responses.post("https://example.test/api/v1/targets/5/files", json={"ok": True}, status=201)

    results = publish_night(config, api, publisher=_make_publisher(config))

    assert results == [{"target_id": 5, "sub_count": 2}]
    assert files_mock.call_count == 2

    import json

    first_body = json.loads(files_mock.calls[0].request.body)
    assert first_body["kind"] == "sub"
    assert first_body["filter"] == "Luminance"
    assert first_body["url"].startswith("https://test-bucket.s3.amazonaws.com/test-scope/test-scope/5/")


def test_skips_directories_it_cannot_map_to_a_target(telescope_config_yaml):
    config = TelescopeConfig.load(telescope_config_yaml)
    api = ObservatoryApiClient(config.api_base_url, config.api_key)

    unmapped_dir = config.subs_dir / "misc_calibration_frames"
    unmapped_dir.mkdir()
    (unmapped_dir / "bias_0001.fits").write_bytes(b"data")

    results = publish_night(config, api, publisher=_make_publisher(config))

    assert results == []
