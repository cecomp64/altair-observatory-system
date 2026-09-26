"""altaird end to end, and the disaster-recovery drill (SPEC §7.9, §15):

frames land on the rig → the daemon collects them → the night closes →
calibration masters, reference, night stack and merge run through a fake
PixInsight → the status page and a night summary go out. Then the catalog is
lost and rebuilt from the NAS alone, without reading any image."""
from __future__ import annotations

import json

from altair import nights
from altair.catalog.db import Catalog
from altair.daemon import Daemon
from altair.storage import nas as nas_mod
from altair.storage.rebuild import FsSource, Rebuilder
from altair.storage.locations import nas_location

from conftest import write_fits
from helpers import Clock, fake_pixinsight, pipeline_config


def shoot_night(rig):
    base = rig / "2026-09-24"
    for n in range(6):
        write_fits(base / "M31" / "LIGHT" / f"L_{n:04d}.fits", date_obs=f"2026-09-25T06:{n:02d}:02", seed=n)
    for n in range(3):
        write_fits(base / "DARK" / f"D_{n}.fits", imagetyp="DARK", object_="", filter_=None, date_obs=f"2026-09-25T12:0{n}:00", seed=100 + n)
        write_fits(base / "FLAT" / f"F_{n}.fits", imagetyp="FLAT", object_="", exposure=2.5, date_obs=f"2026-09-25T12:1{n}:00", seed=200 + n)
        write_fits(base / "DARKFLAT" / f"DF_{n}.fits", imagetyp="DARKFLAT", object_="", filter_=None, exposure=2.5,
                   date_obs=f"2026-09-25T12:2{n}:00", seed=300 + n)


def test_a_night_through_altaird_then_a_catalog_rebuild(tmp_path):
    sent = []
    config = pipeline_config(tmp_path, pixinsight=fake_pixinsight(tmp_path), paths={"published": str(tmp_path / "published")},
                             rigs={"esprit": {**pipeline_config(tmp_path).rigs["esprit"].model_dump(mode="json"),
                                              "collect": {"stable_seconds": 0}}},
                             notifications={"on": ["success", "partial", "failure", "issue_opened", "issue_resolved"],
                                            "channels": [{"type": "capture"}]})
    catalog = Catalog(config.catalog_path)
    nas_mod.init(catalog, config)
    shoot_night(tmp_path / "rig")
    clock = Clock()
    daemon = Daemon(catalog, config, clock=clock)
    from altair.notify import notifier as notifier_mod

    for worker in daemon.workers:   # capture notifications instead of sending them
        if worker.name == "notify":
            n = notifier_mod.Notifier(catalog, config, clock=clock, channels={"capture": lambda cfg, msg: sent.append(msg)})
            worker.fn = lambda n=n: (n.tick(), daemon.write_status())

    errors = daemon.run_once()
    assert all(e is None for e in errors.values()), errors
    assert catalog.one("SELECT count(*) AS n FROM frames")["n"] == 15
    nights.request_close(catalog, config, rig="esprit", night="2026-09-24", closed_by="manual")
    for _ in range(3):   # the collector finishes the close (manifest), then processing runs
        errors = daemon.run_once()
        assert all(e is None for e in errors.values()), errors

    assert catalog.one("SELECT state FROM collections WHERE rig = 'esprit' AND night = '2026-09-24'")["state"] == "closed"
    statuses = {r["kind"]: r["status"] for r in catalog.query("SELECT kind, status FROM jobs")}
    assert statuses == {"CALIB_MASTER": "succeeded", "PROJECT_REFERENCE": "succeeded", "NIGHT_STACK": "succeeded", "MERGE": "succeeded"}
    mn = catalog.one("SELECT * FROM multi_night_masters")
    assert mn and mn["n_nights"] == 1
    status = json.loads((tmp_path / "state" / "ALTAIR_STATUS.json").read_text())
    assert status["projects"][0]["multi_night"][0]["version"] == 1
    assert "<h1>Altair" in (tmp_path / "state" / "ALTAIR_STATUS.html").read_text()
    assert any("night 2026-09-24 on esprit: success" in m.title for m in sent)
    assert not any(w.errors for w in daemon.workers)

    # ── disaster recovery: the catalog is gone; rebuild it from the NAS ──
    expected = {
        "frames": catalog.one("SELECT count(*) AS n FROM frames")["n"],
        "masters": sorted(r["sha256"] for r in catalog.query("SELECT sha256 FROM calibration_masters")),
        "reference": catalog.one("SELECT reference_sha256 FROM projects")["reference_sha256"],
        "night_master": catalog.one("SELECT sha256 FROM night_masters")["sha256"],
        "multi": catalog.one("SELECT sha256, version FROM multi_night_masters")["sha256"],
        "calibrated": catalog.one("SELECT count(*) AS n FROM blobs WHERE data_class = 'calibrated_frame'")["n"],
        "lights": {r["status"] for r in catalog.query("SELECT status FROM frames WHERE image_type = 'light'")},
    }
    rebuilt = Catalog(tmp_path / "rebuilt.sqlite")
    report = Rebuilder(rebuilt, config, [FsSource(nas_location(config))]).run()
    assert report.frames == expected["frames"] and report.frames_without_copy == 0
    assert sorted(r["sha256"] for r in rebuilt.query("SELECT sha256 FROM calibration_masters")) == expected["masters"]
    project = rebuilt.one("SELECT * FROM projects")
    assert project["reference_sha256"] == expected["reference"] and project["path"] == catalog.one("SELECT path FROM projects")["path"]
    nm = rebuilt.one("SELECT * FROM night_masters")
    assert nm["sha256"] == expected["night_master"] and nm["merge_status"] == "merged"
    assert rebuilt.one("SELECT sha256 FROM multi_night_masters")["sha256"] == expected["multi"]
    assert rebuilt.one("SELECT count(*) AS n FROM blobs WHERE data_class = 'calibrated_frame'")["n"] == expected["calibrated"]
    assert {r["status"] for r in rebuilt.query("SELECT status FROM frames WHERE image_type = 'light'")} == expected["lights"]
    assert rebuilt.one("SELECT count(*) AS n FROM replicas WHERE location = 'nas' AND verify_method = 'rebuild'")["n"] > 0
    assert rebuilt.get_state("nas_identity") == catalog.get_state("nas_identity")

    # ── the NAS is replaced: its masters come back from the cache ──
    from altair.storage.locations import remove_file
    from altair.storage.stager import Stager

    nas_root = tmp_path / "nas"
    lost = [p for p in (nas_root / "projects").rglob("*") if p.is_file()] + [p for p in (nas_root / "calibration").rglob("*") if p.is_file()]
    for p in lost:
        remove_file(p)
    plan = Stager(catalog, config).replicate_to_nas(["calibration_master", "project_reference", "night_master", "multi_night_master"], dry_run=True)
    assert plan["planned"] and {source for _, source, _ in plan["planned"]} == {"cache"} and not any(p.exists() for p in lost)
    report = Stager(catalog, config).replicate_to_nas(["calibration_master", "project_reference", "night_master", "multi_night_master"])
    assert report["written"] == len(plan["planned"]) and report["no_source"] == 0
    assert (nas_root / catalog.one("SELECT b.logical_path FROM blobs b JOIN multi_night_masters m USING (sha256)")["logical_path"]).exists()
