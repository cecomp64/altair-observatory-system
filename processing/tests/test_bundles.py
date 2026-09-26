"""Zip bundles in S3 (bundles.py): each final night's calibrated subs as one
zip that replaces per-sub objects, and a per-target masters zip under one
versioned key."""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from datetime import timedelta

import pytest

from altair import bundles
from altair.catalog.db import Catalog
from altair.executor.executor import Executor
from altair.planner.plan import Planner
from altair.storage import blobs
from altair.storage import nas as nas_mod
from altair.storage.cleanup import Cleaner
from altair.storage.locations import S3Location
from altair.storage.replicator import Replicator
from altair.storage.stager import Stager

from helpers import Clock, add_frame, fake_pixinsight, pipeline_config

N1, N2 = "2026-09-24", "2026-09-26"


def night(catalog, config, name, *, lights=6, calib=True, day="2026-09-25"):
    nas = config.storage.nas.root
    for i in range(lights):
        add_frame(catalog, night=name, date_obs=f"{day}T06:{i:02d}:00Z", nas_root=nas)
    if calib:
        for i in range(3):
            add_frame(catalog, "dark", night=name, date_obs=f"{day}T12:0{i}:00Z", nas_root=nas)
            add_frame(catalog, "darkflat", night=name, exposure=2.5, date_obs=f"{day}T12:1{i}:00Z", nas_root=nas)
            add_frame(catalog, "flat", night=name, exposure=2.5, date_obs=f"{day}T12:2{i}:00Z", nas_root=nas)
    Planner(catalog, config).plan_night("esprit", name)
    Executor(catalog, config).run_all()


@pytest.fixture
def site(tmp_path, s3_client):
    config = pipeline_config(tmp_path, s3={}, pixinsight=fake_pixinsight(tmp_path))
    catalog = Catalog(config.catalog_path)
    nas_mod.init(catalog, config)
    s3 = S3Location(config.storage.s3, s3_client)
    night(catalog, config, N1)
    Replicator(catalog, config, s3).run_once()
    return config, catalog, s3, s3_client


def calibrated(catalog):
    return catalog.query("SELECT b.* FROM blobs b WHERE b.data_class = 'calibrated_frame' ORDER BY logical_path")


def test_calibrated_subs_go_to_s3_as_one_zip(site):
    config, catalog, s3, client = site
    subs = calibrated(catalog)
    bundle = catalog.one("SELECT * FROM blobs WHERE data_class = 'calibrated_bundle'")
    assert len(subs) == 6 and bundle["logical_path"].endswith(f"calibrated_{bundle['sha256'][:8]}.zip")
    # One S3 object for the night; no object per sub.
    keys = [o["Key"] for o in client.list_objects_v2(Bucket="astro-archive", Prefix="altair/projects/")["Contents"]]
    assert sum(k.endswith(".zip") and "/calibrated_" in k for k in keys) == 1 and not any("/calibrated/" in k for k in keys)
    body = client.get_object(Bucket="astro-archive", Key=s3.key(bundle["logical_path"]))["Body"].read()
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = sorted(z.namelist())
        assert names[-1] == "manifest.json" and len(names) == 7
        assert z.infolist()[0].compress_type == zipfile.ZIP_STORED
        manifest = json.loads(z.read("manifest.json"))
    assert {f["sha256"] for f in manifest["frames"]} == {s["sha256"] for s in subs}
    for sub in subs:
        reps = blobs.replicas(catalog.conn, sub["sha256"])
        assert "s3" not in reps and blobs.verified(reps["s3_bundle"]) and blobs.verified(reps["nas"])
        member = catalog.one("SELECT * FROM bundle_members WHERE member_sha256 = ?", (sub["sha256"],))
        piece = body[member["data_offset"]:member["data_offset"] + member["size"]]
        assert hashlib.sha256(piece).hexdigest() == sub["sha256"]   # the recorded offsets are exact
    # Subs aren't copied into the cache; the zip is (until evicted after upload).
    assert all("cache" not in blobs.replicas(catalog.conn, s["sha256"]) for s in subs)


def test_a_sub_is_read_back_from_the_zip_even_when_it_is_cold(site):
    config, catalog, s3, client = site
    sub = calibrated(catalog)[0]
    nas_path = blobs.replicas(catalog.conn, sub["sha256"])["nas"]["uri"]
    os.chmod(nas_path, 0o644)
    os.unlink(nas_path)
    with catalog.transaction() as tx:
        blobs.mark_missing(tx, sub["sha256"], "nas", "external_delete")
    bundle = catalog.one("SELECT * FROM blobs WHERE data_class = 'calibrated_bundle'")
    key = s3.key(bundle["logical_path"])
    client.copy_object(Bucket="astro-archive", Key=key, CopySource={"Bucket": "astro-archive", "Key": key}, StorageClass="DEEP_ARCHIVE",
                       MetadataDirective="COPY")
    catalog.execute("UPDATE replicas SET storage_class = 'DEEP_ARCHIVE', state = 'archived_cold' WHERE sha256 = ? AND location = 's3'",
                    (bundle["sha256"],))
    catalog.execute("DELETE FROM replicas WHERE location = 'cache'")
    stager = Stager(catalog, config, s3=s3)
    job = catalog.execute("INSERT INTO jobs(kind, scope_json, plan_json, plan_hash, status) VALUES ('MERGE', '{}', '{}', 'x', 'queued')").lastrowid
    first = stager.stage(job, [sub["sha256"]])
    assert not first.ready and first.restores_pending == 1                     # the zip is restored, whole, once
    assert stager.poll_restores() == 1                                         # moto restores at once
    second = stager.stage(job, [sub["sha256"]])
    assert second.ready and hashlib.sha256(open(second.paths[sub["sha256"]], "rb").read()).hexdigest() == sub["sha256"]
    # ... and the lost NAS copy is written back from it.
    assert blobs.verified(blobs.replicas(catalog.conn, sub["sha256"])["nas"])


def test_nas_cleanup_of_subs_relies_on_a_fresh_check_of_the_zip(site):
    config, catalog, s3, client = site
    later = Clock()
    later.advance(timedelta(days=200).total_seconds())
    report = Cleaner(catalog, config, s3=s3, clock=later).run(dry_run=True, location="nas")
    subs = [c for c in report.deleted if c.rule == "nas.calibrated_frame"]
    assert len(subs) == 6, report.skipped
    relied = json.loads(catalog.one("SELECT relied_on_json FROM cleanup_ledger WHERE rule = 'nas.calibrated_frame'")["relied_on_json"])
    assert relied[0]["checked"] == "head_object" and relied[0]["bundle_sha256"] and relied[0]["member"].endswith(".fits")
    # A zip that is gone from S3 (or changed) protects the NAS copies.
    bundle = catalog.one("SELECT * FROM blobs WHERE data_class = 'calibrated_bundle'")
    for v in client.list_object_versions(Bucket="astro-archive", Prefix=s3.key(bundle["logical_path"]))["Versions"]:
        client.delete_object(Bucket="astro-archive", Key=v["Key"], VersionId=v["VersionId"])
    report = Cleaner(catalog, config, s3=s3, clock=later).run(dry_run=True, location="nas")
    assert not [c for c in report.deleted if c.rule == "nas.calibrated_frame"]
    assert any(why == "no fresh verified S3 copy" for c, why in report.skipped if c.rule == "nas.calibrated_frame")


def test_the_masters_zip_is_rewritten_under_one_versioned_key(site):
    config, catalog, s3, client = site
    first = catalog.one("SELECT * FROM masters_bundles")
    key = s3.key(catalog.one("SELECT logical_path FROM blobs WHERE sha256 = ?", (first["sha256"],))["logical_path"])
    assert key.endswith("/bundles/masters.zip")
    body = client.get_object(Bucket="astro-archive", Key=key)["Body"].read()
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        assert sorted(z.namelist()) == ["README.txt", "T34_Ha_v001.fits", "T34_Ha_v001_report.md"]
    night(catalog, config, N2, lights=8, calib=False, day="2026-09-27")
    Replicator(catalog, config, s3).run_once()
    second = catalog.one("SELECT * FROM masters_bundles")
    assert second["build"] == 2 and second["sha256"] != first["sha256"]
    body = client.get_object(Bucket="astro-archive", Key=key)["Body"].read()
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        assert "T34_Ha_v002.fits" in z.namelist() and "T34_Ha_v001.fits" not in z.namelist()   # finals only
    versions = client.list_object_versions(Bucket="astro-archive", Prefix=key)["Versions"]
    assert len(versions) == 2                          # the old zip is a noncurrent version, expired by lifecycle
    old = blobs.replicas(catalog.conn, first["sha256"])["s3"]
    assert old["state"] == "missing" and old["missing_reason"] == "superseded"
    # Rebuilding with nothing new gives the same zip, and nothing to upload.
    assert bundles.masters(catalog, config, second["project_id"]) == second["sha256"]
    assert Replicator(catalog, config, s3).run_once().uploaded == 0
    from altair.storage.s3_setup import lifecycle_rules

    assert any(r["ID"] == "altair-masters_bundle-noncurrent" for r in lifecycle_rules(config.storage.s3))
