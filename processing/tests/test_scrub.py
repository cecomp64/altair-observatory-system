"""Scrubbing (SPEC §7.8): a missing NAS file is flagged and healed; corrupt
NAS and S3 copies are detected and rewritten from a good copy."""
from __future__ import annotations

import hashlib
import random

import pytest

from altair.storage.scrub import Scrubber

from test_stager import Env


@pytest.fixture
def env(tmp_path, s3_client):
    e = Env(tmp_path, s3_client)
    yield e
    e.catalog.close()


def scrubber(env):
    return Scrubber(env.catalog, env.config, s3=env.s3, rng=random.Random(1))


def test_a_nas_file_deleted_behind_our_back_is_flagged_and_healed_from_s3(env):
    sha, logical = env.blob("L1.fits", s3="STANDARD_IA")
    path = env.nas.path(logical)
    path.chmod(0o644)
    path.unlink()
    report = scrubber(env).run(sample_percent=100)
    assert report.missing == [logical] and report.healed == [logical]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
    assert env.catalog.one("SELECT status FROM issues WHERE kind = 'NAS_FILE_MISSING'")["status"] == "resolved"


def test_a_corrupt_s3_object_is_rewritten_from_the_nas(env):
    sha, logical = env.blob("L1.fits", s3="STANDARD_IA")
    env.client.put_object(Bucket="astro-archive", Key=env.s3.key(logical), Body=b"rot", Metadata={"altair-sha256": sha})
    report = scrubber(env).run(location="s3", sample_percent=100)
    assert report.corrupt == [logical] and report.healed == [logical]
    body = env.client.get_object(Bucket="astro-archive", Key=env.s3.key(logical))["Body"].read()
    assert hashlib.sha256(body).hexdigest() == sha
    versions = env.client.list_object_versions(Bucket="astro-archive", Prefix=env.s3.key(logical))["Versions"]
    assert len(versions) == 3   # the original, the bad one, the repair: versioning keeps them for inspection


def test_nothing_is_scrubbed_while_the_nas_is_unhealthy(env):
    sha, logical = env.blob("L1.fits", s3="STANDARD_IA")
    (env.tmp / "nas" / ".altair-location.json").write_text('{"id": "wrong"}')
    path = env.nas.path(logical)
    path.chmod(0o644)
    path.unlink()
    report = scrubber(env).run(location="nas", sample_percent=100)
    assert report.skipped and not report.missing
    assert env.catalog.one("SELECT state FROM replicas WHERE sha256 = ? AND location = 'nas'", (sha,))["state"] == "present"


def test_scrub_runs_on_its_interval(env):
    s = scrubber(env)
    assert s.due()
    s.run(sample_percent=0)
    assert not s.due()
