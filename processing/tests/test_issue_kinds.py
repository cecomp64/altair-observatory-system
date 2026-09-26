"""Issue kinds added with D3: UNKNOWN_ALIAS, LOCATION_UNREACHABLE,
MANIFEST_MISSING (collector backfill and rebuild rescan), DISK_SPACE_LOW, and
the single rule for a project's multi-night mode."""
from __future__ import annotations

import json

import pytest

from altair.catalog.db import Catalog
from altair.collector import manifest
from altair.ingest.headers import read_header
from altair.ingest.ingest import ingest
from altair.storage.locations import nas_location

from conftest import write_fits
from helpers import add_frame, pipeline_config


@pytest.fixture
def env(tmp_path):
    config = pipeline_config(tmp_path)
    return config, Catalog(config.catalog_path), tmp_path


def _ingest(catalog, config, path):
    return ingest(catalog, config, None, rig="esprit", header=read_header(path), sha256="a" * 64, size=1, logical_path="raw/x.fits",
                  file_name=path.name, replicas=[("nas", str(path))])


def test_an_unknown_telescope_name_is_unknown_alias_and_another_rigs_name_is_unknown_rig(env):
    config, catalog, tmp = env
    result = _ingest(catalog, config, write_fits(tmp / "a.fits", telescop="Mystery Scope 80"))
    assert result.status == "invalid"
    issue = catalog.one("SELECT * FROM issues WHERE kind = 'UNKNOWN_ALIAS'")
    assert "Mystery Scope 80" in issue["message"] and "aliases" in issue["message"]

    config2 = pipeline_config(tmp, rigs={"other": {"telescope": "redcat51", "camera": "asi2600mm", "focal_length_mm": 250}})
    config2.aliases["telescope"]["RedCat"] = "redcat51"
    catalog2 = Catalog(tmp / "c2.sqlite")
    ingest(catalog2, config2, None, rig="esprit", header=read_header(write_fits(tmp / "b.fits", telescop="RedCat 51")), sha256="b" * 64,
           size=1, logical_path="raw/y.fits", file_name="b.fits", replicas=[("nas", str(tmp / "b.fits"))])
    kinds = {r["kind"] for r in catalog2.query("SELECT kind FROM issues")}
    assert kinds == {"UNKNOWN_RIG"}


def test_s3_unreachable_is_a_warning_until_uploads_get_through(env, s3_client, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    from altair.storage.locations import S3Location
    from altair.storage.replicator import Replicator

    config = pipeline_config(env[2], s3={})
    catalog = Catalog(env[2] / "s3.sqlite")
    add_frame(catalog, nas_root=config.storage.nas.root)
    s3 = S3Location(config.storage.s3, s3_client)
    real = S3Location.upload

    def down(self, *a, **kw):
        raise EndpointConnectionError(endpoint_url="https://s3.test")

    monkeypatch.setattr(S3Location, "upload", down)
    Replicator(catalog, config, s3).run_once()
    issue = catalog.one("SELECT * FROM issues WHERE kind = 'LOCATION_UNREACHABLE'")
    assert issue["status"] == "open" and issue["severity"] == "warning"
    assert catalog.one("SELECT reachable FROM locations WHERE name = 's3'")["reachable"] == 0
    monkeypatch.setattr(S3Location, "upload", real)
    Replicator(catalog, config, s3).run_once()
    assert catalog.one("SELECT status FROM issues WHERE kind = 'LOCATION_UNREACHABLE'")["status"] == "resolved"


def test_a_closed_night_without_a_manifest_gets_one_or_an_issue(env):
    config, catalog, tmp = env
    add_frame(catalog, night="2026-09-24", nas_root=config.storage.nas.root)
    catalog.execute("INSERT INTO collections(rig, night, state, closed_by) VALUES ('esprit', '2026-09-24', 'closed', 'manual')")
    catalog.set_state("nas_health", {"reachable": False, "healthy": False, "free_percent": None, "reason": "unreachable"})
    assert manifest.check_missing(catalog, config, nas_location(config)) == [("esprit", "2026-09-24")]
    assert catalog.one("SELECT status FROM issues WHERE kind = 'MANIFEST_MISSING'")["status"] == "open"
    catalog.set_state("nas_health", {"reachable": True, "healthy": True, "free_percent": 50, "reason": None})
    assert manifest.check_missing(catalog, config, nas_location(config)) == []
    assert catalog.one("SELECT manifest_sha256 FROM collections")["manifest_sha256"]
    assert (tmp / "nas" / "raw" / "esprit" / "_manifests" / "2026-09-24.json").exists()
    assert catalog.one("SELECT status FROM issues WHERE kind = 'MANIFEST_MISSING'")["status"] == "resolved"


def test_a_rebuild_rescans_raw_files_no_manifest_lists(env):
    from altair.storage.rebuild import FsSource, Rebuilder

    config, catalog, tmp = env
    write_fits(tmp / "nas" / "raw" / "esprit" / "2026-09-24" / "L_0001.fits", seed=1)
    (tmp / "nas" / "raw" / "esprit" / "2026-09-24" / "junk.fits").write_bytes(b"not fits")
    report = Rebuilder(catalog, config, [FsSource(nas_location(config))]).run()
    assert report.rescanned == 1 and any("junk.fits" in s for s in report.skipped)
    assert catalog.one("SELECT count(*) AS n FROM frames")["n"] == 1
    assert catalog.one("SELECT state FROM collections WHERE night = '2026-09-24'")["state"] == "closed"
    assert catalog.one("SELECT severity FROM issues WHERE kind = 'MANIFEST_MISSING'")["severity"] == "warning"


def test_the_mode_follows_the_settings_and_a_mode_change_updates_both(env, fake_hub):
    from altair.hub.config_sync import HubConfig
    from altair.planner.projects import project_for, set_mode
    from altair.projects.merge import build

    config, catalog, tmp = env
    cfg = pipeline_config(tmp, hub=True)
    payload = fake_hub.config
    with catalog.transaction() as tx:
        project = project_for(tx, cfg, HubConfig.from_payload(payload), rig="esprit", hub_target_id=34)
    assert project["multi_night_mode"] == "master_merge"
    with catalog.transaction() as tx:
        set_mode(tx, project["id"], "frame_reintegration")
    project = catalog.one("SELECT * FROM projects")
    assert json.loads(project["settings_json"])["multi_night"]["mode"] == "frame_reintegration"
    catalog.execute("UPDATE projects SET reference_sha256 = 'r'")
    assert build(catalog, cfg, catalog.one("SELECT * FROM projects"), "Ha", [], [])["plan"]["mode"] == "frame_reintegration"
    # The Hub's target settings change too (ProjectProcessingController#mode); the next pull agrees.
    payload["targets"][0]["processing_settings"]["multi_night"]["mode"] = "frame_reintegration"
    with catalog.transaction() as tx:
        assert project_for(tx, cfg, HubConfig.from_payload(payload), rig="esprit", hub_target_id=34)["multi_night_mode"] == "frame_reintegration"
