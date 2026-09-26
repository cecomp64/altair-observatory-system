"""Staging (SPEC §7.7, phase 2b exit criteria): in-place NAS reads, NAS
healing from the rig or S3, corrupt replicas bypassed, approval guards,
cold restores, DATA_UNAVAILABLE, eviction and the hand-off."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from altair.catalog.db import Catalog
from altair.storage import blobs
from altair.storage import nas as nas_mod
from altair.storage.locations import FsLocation, s3_location
from altair.storage.stager import Stager

from helpers import pipeline_config


class Env:
    def __init__(self, tmp_path, s3_client, **nas):
        self.config = pipeline_config(tmp_path, s3={"max_auto_download_gb": 1, "restore": {"max_auto_restore_gb": 1}})
        for key, value in nas.items():
            setattr(self.config.storage.nas, key, value)
        self.catalog = Catalog(self.config.catalog_path)
        nas_mod.init(self.catalog, self.config)
        nas_mod.check(self.catalog, self.config)
        self.tmp = tmp_path
        self.client = s3_client
        self.s3 = s3_location(self.config, s3_client)
        self.nas = FsLocation("nas", tmp_path / "nas")
        self.stager = Stager(self.catalog, self.config, s3=self.s3)

    def blob(self, name, data_class="raw_light", content=None, *, nas=True, rig=False, s3=None):
        content = content or f"{name}-content".encode() * 100
        sha = hashlib.sha256(content).hexdigest()
        logical = f"raw/esprit/2026-09-24/{name}" if data_class.startswith("raw") else f"calibration/masters/{name}"
        source = self.tmp / "src" / name
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(content)
        with self.catalog.transaction() as tx:
            blobs.add_blob(tx, sha, len(content), data_class, logical, "esprit")
        if nas:
            uri = self.nas.write_verified(source, logical, sha)
            with self.catalog.transaction() as tx:
                blobs.set_replica(tx, sha, "nas", uri)
        if rig:
            rig_path = self.tmp / "rig" / name
            rig_path.write_bytes(content)
            with self.catalog.transaction() as tx:
                blobs.set_replica(tx, sha, "rig:esprit", str(rig_path))
        if s3:
            self.s3.upload(source, logical, sha, data_class)
            if s3 != "STANDARD_IA":
                self.client.copy_object(Bucket="astro-archive", Key=self.s3.key(logical), CopySource={"Bucket": "astro-archive", "Key": self.s3.key(logical)},
                                        StorageClass=s3, MetadataDirective="COPY")
            with self.catalog.transaction() as tx:
                blobs.set_replica(tx, sha, "s3", self.s3.uri(logical), kind="s3", storage_class=s3,
                                  state="archived_cold" if s3 == "DEEP_ARCHIVE" else "present", method="s3_checksum_sha256")
        return sha, logical

    def lose_nas(self, sha, logical, reason="external_delete"):
        path = self.nas.path(logical)
        path.chmod(0o644)
        path.unlink()
        with self.catalog.transaction() as tx:
            blobs.mark_missing(tx, sha, "nas", reason)

    def job(self, inputs):
        return self.catalog.execute("INSERT INTO jobs(kind, scope_json, plan_json, plan_hash, status) VALUES ('NIGHT_STACK', '{}', ?, ?, 'queued')",
                                    (json.dumps({"input_paths": {s: "" for s in inputs}}), os.urandom(8).hex())).lastrowid


@pytest.fixture
def env(tmp_path, s3_client):
    e = Env(tmp_path, s3_client)
    yield e
    e.catalog.close()


def test_raw_frames_are_read_in_place_and_masters_come_through_the_cache(env):
    light, light_path = env.blob("L1.fits")
    master, _ = env.blob("flat.xisf", "calibration_master")
    result = env.stager.stage(env.job([light, master]), [light, master])
    assert result.ready
    assert result.paths[light] == str(env.nas.path(light_path))                  # nothing copied
    assert result.paths[master].startswith(str(env.config.cache_dir))
    assert env.catalog.one("SELECT state FROM replicas WHERE sha256 = ? AND location = 'cache'", (master,))["state"] == "present"


def test_a_lost_nas_copy_is_fetched_from_the_rig_and_written_back(env):
    sha, logical = env.blob("L1.fits", rig=True)
    env.lose_nas(sha, logical)
    result = env.stager.stage(env.job([sha]), [sha])
    assert result.ready and result.paths[sha].startswith(str(env.config.cache_dir))
    assert env.nas.path(logical).exists()
    assert env.catalog.one("SELECT state FROM replicas WHERE sha256 = ? AND location = 'nas'", (sha,))["state"] == "present"


def test_a_lost_nas_copy_is_healed_from_s3_but_a_cleaned_one_is_not_written_back(env):
    healed, healed_path = env.blob("L1.fits", s3="STANDARD_IA")
    cleaned, cleaned_path = env.blob("L2.fits", s3="STANDARD_IA")
    env.lose_nas(healed, healed_path)
    env.lose_nas(cleaned, cleaned_path, reason="cleanup")                    # moved to S3-only on purpose
    result = env.stager.stage(env.job([healed, cleaned]), [healed, cleaned])
    assert result.ready and result.downloaded_bytes > 0
    assert env.nas.path(healed_path).exists() and not env.nas.path(cleaned_path).exists()


def test_a_corrupt_nas_copy_is_detected_and_bypassed(tmp_path, s3_client):
    env = Env(tmp_path, s3_client, nas_read_verify="all")
    sha, logical = env.blob("L1.fits", rig=True)
    path = env.nas.path(logical)
    path.chmod(0o644)
    path.write_bytes(b"flipped bytes")
    result = env.stager.stage(env.job([sha]), [sha])
    assert result.ready and result.paths[sha] != str(path)
    assert env.catalog.one("SELECT status FROM issues WHERE kind = 'INTEGRITY_MISMATCH'")["status"] == "resolved"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha                  # healed


def test_big_s3_downloads_wait_for_approval(env):
    env.config.storage.s3.max_auto_download_gb = 0
    sha, logical = env.blob("L1.fits", s3="STANDARD_IA")
    env.lose_nas(sha, logical)
    job = env.job([sha])
    result = env.stager.stage(job, [sha])
    assert not result.ready and "approval" in result.waiting_reason
    fingerprint = f"FETCH_APPROVAL_NEEDED:job:{job}"
    assert env.catalog.one("SELECT status FROM issues WHERE fingerprint = ?", (fingerprint,))["status"] == "open"
    env.catalog.set_state(f"fetch_decision:{fingerprint}", "approved")
    assert env.stager.stage(job, [sha]).ready
    assert env.catalog.one("SELECT status FROM issues WHERE fingerprint = ?", (fingerprint,))["status"] == "resolved"


def test_deep_archive_objects_are_restored_before_they_are_read(env):
    sha, logical = env.blob("L1.fits", nas=False, s3="DEEP_ARCHIVE")
    job = env.job([sha])
    first = env.stager.stage(job, [sha])
    assert not first.ready and first.restores_pending == 1
    assert env.catalog.one("SELECT state FROM fetch_requests")["state"] == "restoring"
    assert env.catalog.one("SELECT severity FROM issues WHERE kind = 'RESTORE_IN_PROGRESS'")["severity"] == "info"
    assert env.stager.poll_restores() == 1                                       # moto finishes restores at once
    assert env.stager.stage(job, [sha]).ready


def test_no_readable_copy_is_data_unavailable(env):
    sha, logical = env.blob("L1.fits")
    env.lose_nas(sha, logical)
    result = env.stager.stage(env.job([sha]), [sha])
    assert result.waiting_reason == "DATA_UNAVAILABLE"
    issue = env.catalog.one("SELECT message FROM issues WHERE kind = 'DATA_UNAVAILABLE'")
    assert "nas (missing)" in issue["message"]


def test_an_unreachable_nas_means_waiting_not_downloading_whole_nights(env):
    sha, _ = env.blob("L1.fits", s3="STANDARD_IA")
    (env.tmp / "nas").rename(env.tmp / "nas-down")
    nas_mod.check(env.catalog, env.config)
    job = env.job([sha])
    assert "NAS is unreachable" in env.stager.stage(job, [sha]).waiting_reason
    assert env.stager.stage(job, [sha], allow_s3_fallback=True).ready


def test_eviction_is_lru_and_never_drops_pinned_busy_or_sole_copies(env):
    env.config.storage.cache.max_size_gb = 0
    kept_master, _ = env.blob("m.xisf", "calibration_master")
    old_light, old_path = env.blob("L1.fits", s3="STANDARD_IA")
    sole, sole_path = env.blob("L2.fits")
    for sha in (kept_master,):
        env.stager.stage(env.job([sha]), [sha])
    env.lose_nas(old_light, old_path, reason="cleanup")
    env.stager.stage(env.job([old_light]), [old_light])
    env.catalog.execute("UPDATE jobs SET status = 'succeeded'")
    env.lose_nas(sole, sole_path)
    with env.catalog.transaction() as tx:   # pretend only the cache has it
        cache = env.stager.cache_path(sole, sole_path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(b"x")
        blobs.set_replica(tx, sole, "cache", str(cache))
    env.stager.evict()
    cached = {r["sha256"] for r in env.catalog.query("SELECT sha256 FROM replicas WHERE location = 'cache'")}
    assert cached == {kept_master, sole}
    assert env.catalog.one("SELECT status FROM issues WHERE kind = 'CACHE_FULL'")["status"] == "open"


def test_handoff_links_cache_files_under_their_names_and_leaves_nas_paths(env, tmp_path):
    light, light_path = env.blob("L1.fits")
    master, _ = env.blob("flat.xisf", "calibration_master")
    result = env.stager.stage(env.job([light, master]), [light, master])
    out = env.stager.handoff(tmp_path / "work" / "7", result.paths, {master: "master_flat_Ha.xisf"})
    assert out[light] == str(env.nas.path(light_path))
    assert Path(out[master]) == tmp_path / "work" / "7" / "inputs" / "master_flat_Ha.xisf" and Path(out[master]).exists()
