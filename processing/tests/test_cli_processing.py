"""`altair run`, `jobs`, `calib`, `night`, `merge`, `rerun` and `publish` from altair.yaml."""
from __future__ import annotations

import yaml
from click.testing import CliRunner

from altair.catalog.db import Catalog
from altair.cli import main

from conftest import write_fits
from helpers import add_frame, fake_pixinsight, pipeline_config


def test_processing_commands(tmp_path):
    config = pipeline_config(tmp_path, pixinsight=fake_pixinsight(tmp_path), paths={"published": str(tmp_path / "published")})
    path = tmp_path / "altair.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    catalog = Catalog(config.catalog_path)
    nas = config.storage.nas.root
    for i in range(6):
        add_frame(catalog, night="2026-09-24", date_obs=f"2026-09-25T06:{i:02d}:00Z", nas_root=nas, width=60, height=40)
        add_frame(catalog, "dark", night="2026-09-24", date_obs=f"2026-09-25T12:{i:02d}:00Z", nas_root=nas, width=60, height=40)
    catalog.close()
    run = lambda *args: CliRunner().invoke(main, ["--config", str(path), *args], catch_exceptions=False)

    # A flat library from before Altair (60×40 like these frames): imported, it matches the lights.
    flat = tmp_path / "old_flat_Ha.fits"
    write_fits(flat, imagetyp="FLAT", object_="", exposure=2.5, date_obs="2026-09-24T04:00:00", seed=7)
    out = run("calib", "import", str(flat), "--kind", "flat", "--rig", "esprit", "--filter", "Ha", "--rotator", "31250")
    assert "imported FLAT 2026-09-23" in out.output, out.output
    assert "imported" in run("calib", "list", "--kind", "flat").output

    out = run("rerun", "--night", "2026-09-24")
    assert "request 1 rerun" in out.output
    listing = run("jobs").output
    assert "CALIB_MASTER" in listing and "NIGHT_STACK" in listing and "PROJECT_REFERENCE" in listing
    out = run("run")
    assert "0 job(s) still pending" in out.output and "MERGE: succeeded" in out.output
    assert "failed" not in run("jobs").output

    merged = run("merge", "--target", "34", "--filter", "Ha", "--dry-run").output
    assert "2026-09-24 Ha: eligible" in merged and "nothing to merge" in merged   # the plan already succeeded
    out = run("night", "exclude", "--target", "34", "--night", "2026-09-24", "--filter", "Ha")
    assert "night_exclude" in out.output
    assert "blocked - excluded by the user" in run("merge", "--target", "34", "--filter", "Ha").output
    assert "viewing copies written" in run("publish", "--refresh").output
    assert "job 1 queued" in run("rerun", "--job", "1").output


def test_ops_commands(tmp_path):
    config = pipeline_config(tmp_path, pixinsight=fake_pixinsight(tmp_path))
    path = tmp_path / "altair.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    catalog = Catalog(config.catalog_path)
    nas = config.storage.nas.root
    for i in range(6):
        add_frame(catalog, night="2026-09-24", date_obs=f"2026-09-25T06:{i:02d}:00Z", nas_root=nas, width=60, height=40)
        add_frame(catalog, "dark", night="2026-09-24", date_obs=f"2026-09-25T12:{i:02d}:00Z", nas_root=nas, width=60, height=40)
    catalog.close()
    run = lambda *args: CliRunner().invoke(main, ["--config", str(path), *args], catch_exceptions=False)

    run("rerun", "--night", "2026-09-24")
    out = run("serve", "--once").output
    assert "processing: ok" in out and "notify: ok" in out
    assert "FLAT_MISSING" in run("issues", "--open").output
    assert "| esprit | Ha |" in run("issue", "flats-plan").output
    assert run("issue", "show", "1").output.startswith("#1 ")
    status = run("status", "--write").output
    assert "blocking" in status and "status page:" in status and (tmp_path / "state" / "ALTAIR_STATUS.html").exists()
    doctor = run("doctor").output
    assert "[ok] state folder writable" in doctor and "PixInsight executable" in doctor
    assert "standalone mode" in doctor
    assert "waived" in run("issue", "waive", "1", "--note", "testing").output
    rebuilt = run("storage", "rebuild-catalog", "--out", str(tmp_path / "rebuilt.sqlite")).output
    assert "rebuilt" in rebuilt
    assert "exists" in CliRunner().invoke(main, ["--config", str(path), "storage", "rebuild-catalog"]).output
