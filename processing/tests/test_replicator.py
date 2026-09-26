"""The S3 replicator (SPEC §7.5) against moto: priority, windows,
conditional writes, cold classes, and the BACKUP_BEHIND / DATA_AT_RISK
watch."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from altair.catalog.db import Catalog
from altair.collector.rig_worker import RigCollector
from altair.storage import nas as nas_mod
from altair.storage.locations import IntegrityMismatch, s3_location
from altair.storage.replicator import Replicator, in_window

from conftest import write_fits
from helpers import Clock, pipeline_config


@pytest.fixture
def site(tmp_path, s3_client):
    config = pipeline_config(tmp_path, s3={"upload_window": "01:00-05:00"})
    catalog = Catalog(config.catalog_path)
    nas_mod.init(catalog, config)
    clock = Clock(datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc))   # 11:00 local: outside the window
    collector = RigCollector(catalog, config, "esprit", clock=clock, exclusive_open=lambda p: True)
    s3 = s3_location(config, s3_client)

    class Site:
        pass

    s = Site()
    s.config, s.catalog, s.clock, s.collector, s.rig, s.s3, s.client = config, catalog, clock, collector, tmp_path / "rig", s3, s3_client
    s.replicator = Replicator(catalog, config, s3, clock=clock)
    yield s
    catalog.close()


def collect(site, n=2, flats=1):
    for i in range(n):
        write_fits(site.rig / "2026-09-24" / f"L_{i}.fits", date_obs=f"2026-09-25T06:{i:02d}:02", seed=i)
    for i in range(flats):
        write_fits(site.rig / "2026-09-24" / f"F_{i}.fits", imagetyp="FLAT", exposure=2.0, seed=100 + i)
    site.collector.poll()
    site.clock.advance(61)
    site.collector.poll()


def test_raw_lights_go_first_and_ignore_the_window_other_classes_wait(site):
    collect(site)
    manifest = site.rig.parent / "manifest.json"
    manifest.write_text("{}")
    from altair.storage import blobs
    from altair.hashing import sha256_file

    with site.catalog.transaction() as tx:
        blobs.add_blob(tx, sha256_file(manifest), manifest.stat().st_size, "metadata", "raw/esprit/_manifests/x.json", "esprit")
        blobs.set_replica(tx, sha256_file(manifest), "nas", str(manifest))

    report = site.replicator.run_once()
    assert (report.uploaded, report.deferred) == (2, 1)   # raw_calibration isn't backed up; metadata waits for the window
    lights = site.catalog.query("SELECT r.* FROM replicas r JOIN blobs b USING (sha256) WHERE r.location = 's3'")
    assert len(lights) == 2 and all(r["storage_class"] == "STANDARD_IA" and r["verify_method"] == "s3_checksum_sha256" for r in lights)
    obj = site.client.head_object(Bucket="astro-archive", Key="altair/raw/esprit/2026-09-24/L_0.fits")
    assert obj["Metadata"]["altair-sha256"] == site.catalog.one("SELECT sha256 FROM frames WHERE file_name = 'L_0.fits'")["sha256"]
    tags = site.client.get_object_tagging(Bucket="astro-archive", Key="altair/raw/esprit/2026-09-24/L_0.fits")["TagSet"]
    assert {"Key": "altair-class", "Value": "raw_light"} in tags

    site.clock.now = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)   # 03:00 local
    assert site.replicator.run_once().uploaded == 1


def test_an_existing_object_is_never_overwritten(site):
    collect(site, n=1, flats=0)
    frame = site.catalog.one("SELECT sha256 FROM frames")
    site.client.put_object(Bucket="astro-archive", Key="altair/raw/esprit/2026-09-24/L_0.fits", Body=b"something else",
                           Metadata={"altair-sha256": "f" * 64})
    report = site.replicator.run_once()
    assert report.uploaded == 0 and "not overwritten" in report.failed[0]
    assert site.client.get_object(Bucket="astro-archive", Key="altair/raw/esprit/2026-09-24/L_0.fits")["Body"].read() == b"something else"
    assert site.catalog.one("SELECT status FROM issues WHERE kind = 'INTEGRITY_MISMATCH'")["status"] == "open"
    assert not site.catalog.one("SELECT 1 FROM replicas WHERE sha256 = ? AND location = 's3'", (frame["sha256"],))


def test_the_same_object_already_there_counts_as_backed_up(site):
    collect(site, n=1, flats=0)
    source = site.replicator.source_path(site.catalog.one("SELECT sha256 FROM frames")["sha256"])
    row = site.catalog.one("SELECT * FROM blobs")
    site.s3.upload(source, row["logical_path"], row["sha256"], "raw_light")   # e.g. a crash after upload, before the catalog update
    assert site.replicator.run_once().uploaded == 1
    assert site.catalog.one("SELECT state FROM replicas WHERE location = 's3'")["state"] == "present"


def test_multipart_uploads_carry_checksums_and_resume(site, tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(bytes(range(256)) * 50_000)   # 12.8 MB, 5 MB parts
    site.s3.cfg.multipart_chunk_mb = 5
    from altair.hashing import sha256_file

    sha = sha256_file(big)
    info = site.s3.upload(big, "projects/T1/nights/2026-09-24/Ha/calibrated/x.xisf", sha, "calibrated_frame")
    assert info.sha256 == sha and info.size == big.stat().st_size
    with pytest.raises(IntegrityMismatch):
        other = tmp_path / "other.bin"
        other.write_bytes(b"x" * 100)
        site.s3.upload(other, "projects/T1/nights/2026-09-24/Ha/calibrated/x.xisf", sha256_file(other), "calibrated_frame")


def test_object_lock_retention_on_kept_classes(site):
    site.s3.cfg.object_lock = site.s3.cfg.object_lock or __import__("altair.config", fromlist=["ObjectLock"]).ObjectLock()
    collect(site, n=1, flats=0)
    site.replicator.run_once()
    head = site.client.head_object(Bucket="astro-archive", Key="altair/raw/esprit/2026-09-24/L_0.fits")
    assert head["ObjectLockMode"] == "GOVERNANCE" and head["ObjectLockRetainUntilDate"].year >= 2036


def test_backup_behind_and_data_at_risk(site):
    collect(site, n=1, flats=0)
    site.clock.advance(7 * 3600)
    site.replicator.check_backup()
    assert site.catalog.one("SELECT status FROM issues WHERE kind = 'BACKUP_BEHIND'")["status"] == "open"
    with site.catalog.transaction() as tx:   # the NAS copy is lost too
        tx.execute("UPDATE replicas SET state = 'missing', verified_at = NULL WHERE location = 'nas'")
    site.clock.advance(48 * 3600)
    site.replicator.check_backup()
    assert site.catalog.one("SELECT severity FROM issues WHERE kind = 'DATA_AT_RISK'")["severity"] == "blocking"

    with site.catalog.transaction() as tx:
        tx.execute("UPDATE replicas SET state = 'present', verified_at = '2026-09-25T00:00:00Z' WHERE location = 'nas'")
    site.clock.now = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
    site.replicator.run_once()
    assert {r["kind"]: r["status"] for r in site.catalog.query("SELECT kind, status FROM issues")} == {
        "BACKUP_BEHIND": "resolved", "DATA_AT_RISK": "resolved"}


def test_upload_window():
    tz = "America/Los_Angeles"
    assert in_window("01:00-05:00", datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc), tz)       # 03:00 local
    assert not in_window("01:00-05:00", datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc), tz)
    assert in_window("22:00-06:00", datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc), tz)        # 23:00 local, wraps
    assert in_window(None, datetime.now(timezone.utc), tz)
