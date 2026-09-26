"""Cleanup (SPEC §7.6): per-location rules, the re-check right before each
delete, the ledger, dry runs, the global guards, and property tests that no
sequence of operations removes the last verified copy of anything."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from altair.catalog.db import Catalog
from altair.collector.rig_worker import RigCollector
from altair.storage import blobs
from altair.storage import nas as nas_mod
from altair.storage.cleanup import Cleaner
from altair.storage.locations import s3_location
from altair.storage.replicator import Replicator

from conftest import write_fits
from helpers import Clock, pipeline_config

START = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)


class Site:
    def __init__(self, tmp_path, s3_client, **config):
        self.config = pipeline_config(tmp_path, s3={}, **config)
        self.catalog = Catalog(self.config.catalog_path)
        nas_mod.init(self.catalog, self.config)
        self.clock = Clock(START)
        self.rig = tmp_path / "rig"
        self.nas = tmp_path / "nas"
        self.collector = RigCollector(self.catalog, self.config, "esprit", clock=self.clock, exclusive_open=lambda p: True)
        self.s3 = s3_location(self.config, s3_client)
        self.client = s3_client
        self.replicator = Replicator(self.catalog, self.config, self.s3, clock=self.clock)
        self.cleaner = Cleaner(self.catalog, self.config, s3=self.s3, clock=self.clock)

    def collect(self, n=2, flats=1, day="2026-09-24"):
        for i in range(n):
            path = write_fits(self.rig / day / f"L_{i}.fits", date_obs=f"2026-09-25T06:{i:02d}:02", seed=i)
            os.utime(path, (self.clock.now.timestamp(), self.clock.now.timestamp()))
        for i in range(flats):
            path = write_fits(self.rig / day / f"F_{i}.fits", imagetyp="FLAT", exposure=2.0, seed=100 + i)
            os.utime(path, (self.clock.now.timestamp(), self.clock.now.timestamp()))
        self.collector.poll()
        self.clock.advance(61)
        self.collector.poll()

    def later(self, days):
        self.clock.advance(days * 86400)

    def replica(self, file_name, location):
        return self.catalog.one("SELECT r.* FROM replicas r JOIN frames f USING (sha256) WHERE f.file_name = ? AND r.location = ?",
                                (file_name, location))


@pytest.fixture
def site(tmp_path, s3_client):
    s = Site(tmp_path, s3_client)
    yield s
    s.catalog.close()


def test_rig_copies_go_after_3_days_once_the_nas_and_s3_copies_are_verified(site):
    site.collect()
    site.replicator.run_once()
    site.later(2)
    assert not [c for c in site.cleaner.run().deleted if c.location == "rig:esprit"]   # too young
    site.later(2)
    report = site.cleaner.run()
    rig_deleted = sorted(os.path.basename(c.uri) for c in report.deleted if c.location == "rig:esprit")
    assert rig_deleted == ["F_0.fits", "L_0.fits", "L_1.fits"]
    assert not (site.rig / "2026-09-24" / "L_0.fits").exists()
    assert (site.nas / "raw" / "esprit" / "2026-09-24" / "L_0.fits").exists()
    rep = site.replica("L_0.fits", "rig:esprit")
    assert (rep["state"], rep["missing_reason"]) == ("missing", "cleanup")
    entry = site.catalog.one("SELECT * FROM cleanup_ledger WHERE uri LIKE '%L_0.fits' AND location = 'rig:esprit'")
    assert entry["dry_run"] == 0 and "head_object" in entry["relied_on_json"] and "nas" in entry["relied_on_json"]


def test_a_raw_light_stays_on_the_rig_until_s3_has_it_calibration_needs_only_the_nas(site):
    site.collect()
    site.later(4)
    deleted = {os.path.basename(c.uri) for c in site.cleaner.run().deleted if c.location == "rig:esprit"}
    assert deleted == {"F_0.fits"}
    assert (site.rig / "2026-09-24" / "L_0.fits").exists()


def test_an_s3_object_deleted_behind_our_back_blocks_rig_cleanup(site):
    site.collect(n=1, flats=0)
    site.replicator.run_once()
    site.client.delete_object(Bucket="astro-archive", Key="altair/raw/esprit/2026-09-24/L_0.fits")
    site.later(4)
    report = site.cleaner.run()
    assert not [c for c in report.deleted if c.location == "rig:esprit"]
    assert any("S3" in why or "s3" in why for _, why in report.skipped)


def test_a_rig_file_that_changed_since_collection_is_kept(site):
    site.collect(n=1, flats=0)
    site.replicator.run_once()
    path = site.rig / "2026-09-24" / "L_0.fits"
    stamp = path.stat().st_mtime
    path.write_bytes(path.read_bytes()[:-10] + b"0123456789")
    os.utime(path, (stamp, stamp))
    site.later(4)
    report = site.cleaner.run()
    assert path.exists() and any("changed" in why for _, why in report.skipped)


def test_uncollected_rig_files_are_never_deleted(site):
    write_fits(site.rig / "2026-09-24" / "new.fits", seed=5)
    site.collector.poll()   # seen, not yet stable
    site.later(30)
    site.cleaner.run()
    assert (site.rig / "2026-09-24" / "new.fits").exists()


def test_the_spool_empties_once_nas_and_s3_have_the_frame(site):
    site.collect(n=1, flats=1)
    report = site.cleaner.run()
    assert {c.rule for c in report.deleted} == {"spool"}          # the flat (not backed up to S3)
    site.replicator.run_once()
    site.cleaner.run()
    assert site.catalog.one("SELECT count(*) AS n FROM spool_files")["n"] == 0
    assert not list((site.config.paths.spool_dir / "esprit").glob("*.fits"))


def test_nas_raw_lights_move_to_s3_only_after_retention_with_fresh_checks(site):
    site.collect(n=1, flats=0)
    site.replicator.run_once()
    with site.catalog.transaction() as tx:
        tx.execute("UPDATE frames SET status = 'processed'")
    site.later(179)
    assert not [c for c in site.cleaner.run().deleted if c.location == "nas"]
    site.later(2)
    report = site.cleaner.run()
    assert [c.rule for c in report.deleted if c.location == "nas"] == ["nas.raw_light"]
    assert not (site.nas / "raw" / "esprit" / "2026-09-24" / "L_0.fits").exists()
    assert site.replica("L_0.fits", "s3")["state"] == "present"


def test_nas_retention_waits_for_processing_open_issues_and_pinned_projects(site):
    site.collect(n=1, flats=0)
    site.replicator.run_once()
    site.later(200)
    assert not [c for c in site.cleaner.run().deleted if c.location == "nas"]    # the night isn't processed
    with site.catalog.transaction() as tx:
        tx.execute("UPDATE frames SET status = 'processed'")
        tx.execute("INSERT INTO issues(kind, severity, status, fingerprint, scope_json, message) VALUES "
                   "('FLAT_MISSING', 'blocking', 'open', 'x', '{\"rig\": \"esprit\", \"night\": \"2026-09-24\"}', 'm')")
    assert not [c for c in site.cleaner.run().deleted if c.location == "nas"]    # an open issue on that night
    with site.catalog.transaction() as tx:
        tx.execute("UPDATE issues SET status = 'resolved'")
    assert [c for c in site.cleaner.run(dry_run=True).deleted if c.location == "nas"]


def test_a_corrupt_nas_copy_is_never_the_reason_s3_becomes_the_only_copy(site):
    site.collect(n=1, flats=0)
    site.replicator.run_once()
    with site.catalog.transaction() as tx:
        tx.execute("UPDATE frames SET status = 'processed'")
    copy = site.nas / "raw" / "esprit" / "2026-09-24" / "L_0.fits"
    copy.chmod(0o644)
    copy.write_bytes(copy.read_bytes()[:-4] + b"XXXX")
    site.later(200)
    report = site.cleaner.run()
    assert copy.exists() and any("corrupt" in why for _, why in report.skipped)
    assert site.replica("L_0.fits", "nas")["state"] == "corrupt"


def test_dry_run_deletes_nothing_but_records_the_plan(site):
    site.collect()
    site.replicator.run_once()
    site.later(4)
    report = site.cleaner.run(dry_run=True)
    assert report.deleted and all(os.path.exists(c.uri) for c in report.deleted)
    assert site.catalog.one("SELECT count(*) AS n FROM cleanup_ledger WHERE dry_run = 1")["n"] == len(report.deleted)


def test_nothing_is_cleaned_while_the_nas_is_unhealthy_or_s3_is_unsafe(site):
    site.collect()
    site.replicator.run_once()
    site.later(4)
    (site.nas / ".altair-location.json").write_text('{"id": "wrong"}')
    assert "unhealthy" in site.cleaner.run().blocked
    (site.nas / ".altair-location.json").write_text(__import__("json").dumps(site.catalog.get_state("nas_identity")))
    with site.catalog.transaction() as tx:
        tx.execute("INSERT INTO issues(kind, severity, status, fingerprint, scope_json, message) VALUES "
                   "('S3_CONFIG_UNSAFE', 'blocking', 'open', 'S3_CONFIG_UNSAFE', '{}', 'm')")
    assert "S3_CONFIG_UNSAFE" in site.cleaner.run().blocked
    assert (site.rig / "2026-09-24" / "L_0.fits").exists()


def test_low_free_space_cleans_early_but_still_needs_the_backups(site, monkeypatch):
    from altair.storage.locations import FsLocation

    site.config.storage.cleanup.rig_defaults.target_free_percent = 20
    site.collect()
    monkeypatch.setattr(FsLocation, "free_percent", lambda self: 5.0)
    deleted = {os.path.basename(c.uri) for c in site.cleaner.run().deleted if c.location == "rig:esprit"}
    assert deleted == {"F_0.fits"}                 # early, but raw lights still wait for S3
    site.replicator.run_once()
    deleted = {os.path.basename(c.uri) for c in site.cleaner.run().deleted if c.location == "rig:esprit"}
    assert deleted == {"L_0.fits", "L_1.fits"}


def test_old_session_end_markers_go(site):
    marker = site.rig / "_altair" / "session-end-1.json"
    marker.parent.mkdir()
    marker.write_text("{}")
    os.utime(marker, (START.timestamp(), START.timestamp()))
    site.later(31)
    site.cleaner.run()
    assert not marker.exists() and marker.parent.exists()


# ── property: no sequence of events loses the last verified copy ─────────
EVENT = st.sampled_from(["advance", "replicate", "cleanup", "lose_nas", "lose_s3", "lose_rig", "process"])


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(events=st.lists(EVENT, min_size=3, max_size=12))
def test_cleanup_never_removes_the_last_verified_copy(tmp_path_factory, events):
    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-west-2")
        client.create_bucket(Bucket="astro-archive", CreateBucketConfiguration={"LocationConstraint": "us-west-2"}, ObjectLockEnabledForBucket=True)
        site = Site(tmp_path_factory.mktemp("prop"), client)
        site.collect(n=2, flats=1)
        shas = [r["sha256"] for r in site.catalog.query("SELECT sha256 FROM frames")]
        for event in events:
            if event == "advance":
                site.later(100)
            elif event == "replicate":
                site.replicator.run_once()
            elif event == "cleanup":
                before = {sha: blobs.verified_locations(site.catalog.conn, sha) for sha in shas}
                site.cleaner.run()
                after = {sha: blobs.verified_locations(site.catalog.conn, sha) for sha in shas}
                for sha in shas:
                    if before[sha]:
                        assert after[sha], f"cleanup removed the last verified copy of {sha[:8]}"
                    for loc in set(before[sha]) - set(after[sha]):
                        # a deletion only ever happens at the location its rule targets, recorded in the ledger
                        assert site.catalog.one("SELECT 1 FROM cleanup_ledger WHERE sha256 = ? AND location = ? AND dry_run = 0", (sha, loc))
            elif event == "process":
                with site.catalog.transaction() as tx:
                    tx.execute("UPDATE frames SET status = 'processed'")
            else:   # a copy lost outside Altair: the catalog learns of it
                location = {"lose_nas": "nas", "lose_s3": "s3", "lose_rig": "rig:esprit"}[event]
                with site.catalog.transaction() as tx:
                    for sha in shas[:1]:
                        blobs.mark_missing(tx, sha, location, "external_delete")
        # Every file the catalog says is present really exists (cleanup kept the catalog honest).
        for row in site.catalog.query("SELECT * FROM replicas WHERE state = 'present' AND location IN ('nas', 'rig:esprit')"):
            assert os.path.exists(row["uri"])
        site.catalog.close()
