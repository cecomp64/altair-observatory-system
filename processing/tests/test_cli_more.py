"""The commands added with D2: equipment, ingest, project rereference/set-mode,
run and status filters, calib build, restore-catalog --at, s3 apply-lifecycle."""
from __future__ import annotations

import yaml
from click.testing import CliRunner

from altair.catalog.db import Catalog
from altair.cli import main

from conftest import write_fits
from helpers import add_frame, fake_pixinsight, pipeline_config

NIGHT = "2026-09-24"


def setup(tmp_path, **extra):
    config = pipeline_config(tmp_path, pixinsight=fake_pixinsight(tmp_path), **extra)
    path = tmp_path / "altair.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    catalog = Catalog(config.catalog_path)
    nas = config.storage.nas.root
    for i in range(6):
        add_frame(catalog, night=NIGHT, date_obs=f"2026-09-25T06:{i:02d}:00Z", nas_root=nas)
    for i in range(3):
        add_frame(catalog, "dark", night=NIGHT, date_obs=f"2026-09-25T12:0{i}:00Z", nas_root=nas)
        add_frame(catalog, "darkflat", night=NIGHT, exposure=2.5, date_obs=f"2026-09-25T12:1{i}:00Z", nas_root=nas)
        add_frame(catalog, "flat", night=NIGHT, exposure=2.5, date_obs=f"2026-09-25T12:2{i}:00Z", nas_root=nas)
    catalog.close()
    return config, lambda *args, **kw: CliRunner().invoke(main, ["--config", str(path), *args], catch_exceptions=False, **kw)


def test_calib_build_then_filtered_runs_and_status(tmp_path):
    config, run = setup(tmp_path)
    out = run("calib", "build", "--night", NIGHT).output
    assert out.count("succeeded") == 3, out
    catalog = Catalog(config.catalog_path)
    assert catalog.one("SELECT count(*) AS n FROM calibration_masters")["n"] == 3
    assert {r["status"] for r in catalog.query("SELECT status FROM jobs WHERE kind != 'CALIB_MASTER'")} == {"queued"}
    out = run("run", "--kind", "PROJECT_REFERENCE").output
    assert "PROJECT_REFERENCE: succeeded" in out and "NIGHT_STACK" not in out
    out = run("run", "--night", "2026-09-30").output          # nothing that night
    assert "succeeded" not in out
    out = run("run", "--target", "34").output                 # the stack, then the merge it queues
    assert "NIGHT_STACK: succeeded" in out and "MERGE: succeeded" in out
    status = run("status", "--night", NIGHT, "--target", "34").output
    assert f"{NIGHT} Ha: final, 6 frames, merged" in status
    assert "T34" not in run("status", "--target", "99").output


def test_equipment_events_replan_and_block_the_merge(tmp_path):
    config, run = setup(tmp_path)
    run("rerun", "--night", NIGHT)
    run("run")
    catalog = Catalog(config.catalog_path)
    assert catalog.one("SELECT merge_status FROM night_masters WHERE kind = 'final'")["merge_status"] == "merged"
    # A reducer change between the lights (06:00 UTC) and the flats (12:20 UTC) splits them.
    out = run("equipment", "log", "--rig", "esprit", "reducer_changed", "--at", "2026-09-25T09:00:00Z", "--note", "0.8x reducer").output
    assert "recorded" in out
    assert "reducer_changed" in run("equipment", "list").output
    run("run")
    issue = catalog.one("SELECT * FROM issues WHERE kind = 'FLAT_MISSING'")
    assert issue["status"] == "open" and "reducer_changed" in issue["message"]
    nm = catalog.one("SELECT * FROM night_masters WHERE kind = 'final' AND superseded_by IS NULL")
    assert nm["merge_status"] == "blocked" and "FLAT_MISSING" in nm["merge_block_reason"]


def test_project_set_mode_and_rereference(tmp_path):
    config, run = setup(tmp_path)
    run("rerun", "--night", NIGHT)
    run("run")
    assert "frame_reintegration" in run("project", "set-mode", "--target", "34", "frame_reintegration").output
    catalog = Catalog(config.catalog_path)
    assert catalog.one("SELECT multi_night_mode FROM projects")["multi_night_mode"] == "frame_reintegration"
    out = run("project", "rereference", "--target", "34", "--yes").output
    assert "6 lights" in out and "from nas" in out and "1 night(s) re-planned" in out
    assert catalog.one("SELECT reference_version FROM projects")["reference_version"] == 2
    assert catalog.one("SELECT status FROM issues WHERE kind = 'STALE_REFERENCE'")["status"] == "open"
    assert "Aborted" in run("project", "rereference", "--target", "34", input="n\n").output


def test_ingest_files_and_folders(tmp_path):
    config, run = setup(tmp_path)
    one = write_fits(tmp_path / "loose" / "L_9001.fits", seed=91)
    write_fits(tmp_path / "folder" / "sub" / "L_9002.fits", seed=92)
    out = run("ingest", str(one), str(tmp_path / "folder"), "--rig", "esprit").output
    assert out.count("1 ingested") == 2, out
    catalog = Catalog(config.catalog_path)
    paths = [r["logical_path"] for r in catalog.query("SELECT logical_path FROM blobs WHERE logical_path LIKE '%L_900%' ORDER BY 1")]
    assert paths == ["raw/esprit/2026-09-24/L_9001.fits", "raw/esprit/2026-09-24/L_9002.fits"]


def test_restore_catalog_at_a_time(tmp_path):
    from datetime import datetime, timezone

    from altair.storage import catalog_backup
    from altair.storage.locations import nas_location

    config, run = setup(tmp_path)
    catalog = Catalog(config.catalog_path)
    catalog_backup.backup(catalog, config, nas_location(config), now=datetime(2026, 9, 20, 11, tzinfo=timezone.utc))
    catalog.set_state("marker", "second")
    catalog_backup.backup(catalog, config, nas_location(config), now=datetime(2026, 9, 22, 11, tzinfo=timezone.utc))
    catalog.close()
    run("storage", "restore-catalog", "--at", "2026-09-21T00:00:00Z")
    assert Catalog(config.catalog_path).get_state("marker") is None
    run("storage", "restore-catalog")
    assert Catalog(config.catalog_path).get_state("marker") == "second"
    assert "no catalog backup" in CliRunner().invoke(main, ["--config", str(tmp_path / "altair.yaml"), "storage", "restore-catalog",
                                                            "--at", "2026-01-01"]).output


def test_s3_apply_lifecycle(tmp_path, s3_client, monkeypatch):
    import boto3

    config, run = setup(tmp_path, s3={"lifecycle": {"raw_light": {"to": "DEEP_ARCHIVE", "after_days": 120}}})
    monkeypatch.setattr(boto3.session.Session, "client", lambda self, *a, **kw: s3_client)
    out = run("storage", "s3", "apply-lifecycle").output
    assert "altair-raw_light-to-deep_archive" in out
    rules = s3_client.get_bucket_lifecycle_configuration(Bucket="astro-archive")["Rules"]
    assert [r["ID"] for r in rules] == ["altair-raw_light-to-deep_archive"]
