"""The collector (SPEC §7.3): stability, double read, verified NAS writes,
outages, files that vanish, closing nights with manifests, markers and late
frames. The rig share and the NAS are folders under tmp_path."""
from __future__ import annotations

import json
import os
import stat

import pytest

from altair import nights
from altair.catalog.db import Catalog
from altair.collector import rig_worker
from altair.collector.rig_worker import RigCollector
from altair.storage import nas as nas_mod

from conftest import write_fits
from helpers import Clock, pipeline_config


@pytest.fixture
def site(tmp_path):
    config = pipeline_config(tmp_path)
    catalog = Catalog(config.catalog_path)
    nas_mod.init(catalog, config)
    clock = Clock()
    collector = RigCollector(catalog, config, "esprit", clock=clock, exclusive_open=lambda p: True)

    class Site:
        pass

    s = Site()
    s.config, s.catalog, s.clock, s.collector, s.rig, s.nas = config, catalog, clock, collector, tmp_path / "rig", tmp_path / "nas"
    yield s
    catalog.close()


def light(site, n, night_dir="2026-09-24", **kw):
    return write_fits(site.rig / night_dir / "M31" / "LIGHT" / "Ha" / f"L_{n:04d}.fits", date_obs=f"2026-09-25T06:{n:02d}:02", seed=n, **kw)


def settle(site):
    """Two polls a minute apart: first sight, then stable."""
    first = site.collector.poll()
    site.clock.advance(61)
    return first, site.collector.poll()


def test_collects_stable_frames_into_the_nas_verified_and_read_only(site):
    light(site, 1)
    light(site, 2)
    first, second = settle(site)

    assert first.collected == 0 and first.seen == 2
    assert second.collected == 2
    rel = "2026-09-24/M31/LIGHT/Ha/L_0001.fits"
    copy = site.nas / "raw" / "esprit" / "2026-09-24" / "M31" / "LIGHT" / "Ha" / "L_0001.fits"
    assert copy.read_bytes() == (site.rig / rel).read_bytes()
    assert not os.access(copy, os.W_OK) or os.geteuid() == 0
    assert not (copy.stat().st_mode & stat.S_IWUSR)
    frame = site.catalog.one("SELECT * FROM frames WHERE file_name = 'L_0001.fits'")
    assert (frame["image_type"], frame["filter"], frame["night"], frame["status"]) == ("light", "Ha", "2026-09-24", "valid")
    replicas = {r["location"]: r for r in site.catalog.query("SELECT * FROM replicas WHERE sha256 = ?", (frame["sha256"],))}
    assert set(replicas) == {"nas", "rig:esprit"} and all(r["verified_at"] for r in replicas.values())
    assert site.catalog.one("SELECT state FROM rig_files WHERE rel_path = ?", (rel,))["state"] == "collected"
    assert site.catalog.one("SELECT state FROM collections WHERE rig = 'esprit' AND night = '2026-09-24'")["state"] == "open"
    assert site.catalog.one("SELECT count(*) AS n FROM spool_files")["n"] == 2


def test_waits_while_a_file_is_still_changing_or_open(site, monkeypatch):
    path = light(site, 1)
    site.collector.poll()
    site.clock.advance(61)
    path.write_bytes(path.read_bytes() + b"\0" * 2880)   # NINA still writing: size changed
    assert site.collector.poll().collected == 0
    site.clock.advance(61)
    site.collector.exclusive_open = lambda p: False       # ...or still has it open
    assert site.collector.poll().collected == 0
    site.collector.exclusive_open = lambda p: True
    assert site.collector.poll().collected == 1


def test_truncated_frames_are_not_collected(site):
    path = light(site, 1)
    path.write_bytes(path.read_bytes()[:4000])
    _, report = settle(site)
    assert report.collected == 0
    assert site.catalog.one("SELECT state, last_error FROM rig_files")["last_error"].startswith("incomplete")


def test_a_read_mismatch_discards_the_copy_and_three_raise_collection_corrupt(site, monkeypatch):
    light(site, 1)
    monkeypatch.setattr(rig_worker, "sha256_file", lambda p: "0" * 64)
    site.collector.poll()
    for _ in range(3):
        site.clock.advance(61)
        assert site.collector.poll().collected == 0
    assert not list((site.nas / "raw").rglob("*.fits"))
    assert site.catalog.one("SELECT status FROM issues WHERE kind = 'COLLECTION_CORRUPT'")["status"] == "open"
    monkeypatch.undo()
    site.clock.advance(61)
    assert site.collector.poll().collected == 1
    assert site.catalog.one("SELECT status FROM issues WHERE kind = 'COLLECTION_CORRUPT'")["status"] == "resolved"


def test_nothing_is_collected_while_the_nas_is_unreachable_or_unhealthy(site):
    light(site, 1)
    site.nas.rename(site.nas.with_name("nas-offline"))
    _, report = settle(site)
    assert (report.nas_usable, report.collected) == (False, 0)
    assert site.catalog.one("SELECT status FROM issues WHERE kind = 'NAS_UNREACHABLE'")["status"] == "open"

    site.nas.with_name("nas-offline").rename(site.nas)
    (site.nas / ".altair-location.json").write_text(json.dumps({"id": "someone-else"}))
    assert site.collector.poll().collected == 0
    assert site.catalog.one("SELECT status FROM issues WHERE kind = 'NAS_UNHEALTHY'")["status"] == "open"

    (site.nas / ".altair-location.json").write_text(json.dumps(site.catalog.get_state("nas_identity")))
    assert site.collector.poll().collected == 1
    assert {r["kind"]: r["status"] for r in site.catalog.query("SELECT kind, status FROM issues")} == {
        "NAS_UNREACHABLE": "resolved", "NAS_UNHEALTHY": "resolved"}


def test_an_unreachable_rig_warns_then_blocks_and_recovers(site):
    light(site, 1)
    site.collector.poll()
    site.rig.rename(site.rig.with_name("rig-offline"))
    assert site.collector.poll().reachable is False
    site.clock.advance(31 * 60)
    site.collector.poll()
    issue = site.catalog.one("SELECT severity, status FROM issues WHERE kind = 'RIG_UNREACHABLE'")
    assert (issue["severity"], issue["status"]) == ("warning", "open")
    site.clock.advance(12 * 3600)
    site.collector.poll()
    assert site.catalog.one("SELECT severity FROM issues WHERE kind = 'RIG_UNREACHABLE'")["severity"] == "blocking"
    site.rig.with_name("rig-offline").rename(site.rig)
    site.clock.advance(61)
    assert site.collector.poll().collected == 1
    assert site.catalog.one("SELECT status FROM issues WHERE kind = 'RIG_UNREACHABLE'")["status"] == "resolved"


def test_a_file_deleted_on_the_rig_before_collection_is_data_at_risk(site):
    path = light(site, 1)
    site.collector.poll()
    path.unlink()
    site.collector.poll()
    issue = site.catalog.one("SELECT severity, status FROM issues WHERE kind = 'DATA_AT_RISK'")
    assert (issue["severity"], issue["status"]) == ("blocking", "open")


def test_closing_a_night_writes_the_manifest_then_closes(site):
    light(site, 1)
    write_fits(site.rig / "2026-09-24" / "FLAT" / "F_0001.fits", imagetyp="FLAT", object_="FlatWizard", exposure=2.5, seed=99)
    settle(site)
    assert nights.request_close(site.catalog, site.config, rig="esprit", night="2026-09-24", closed_by="session_end",
                                at="2026-09-25T12:40:00Z") == "closing"
    report = site.collector.poll()
    assert report.closed == ["2026-09-24"]
    night = site.catalog.one("SELECT * FROM collections WHERE rig = 'esprit' AND night = '2026-09-24'")
    assert (night["state"], night["closed_by"], night["n_files"]) == ("closed", "session_end", 2)
    manifest = json.loads((site.nas / "raw" / "esprit" / "_manifests" / "2026-09-24.json").read_text())
    assert manifest["night"] == "2026-09-24" and len(manifest["files"]) == 2
    entry = next(f for f in manifest["files"] if f["rig_path"].endswith("L_0001.fits"))
    assert entry["class"] == "raw_light" and entry["headers"]["FILTER"] == "H-alpha"
    blob = site.catalog.one("SELECT data_class FROM blobs WHERE sha256 = ?", (night["manifest_sha256"],))
    assert blob["data_class"] == "metadata"
    assert site.catalog.one("SELECT kind FROM plan_requests WHERE kind = 'night_ready'")


def test_a_night_waits_for_its_last_frames_and_reports_stuck_ones(site):
    light(site, 1)
    site.collector.poll()
    nights.request_close(site.catalog, site.config, rig="esprit", night="2026-09-24", closed_by="quiescence")
    site.collector.exclusive_open = lambda p: False
    site.clock.advance(61)
    assert site.collector.poll().closed == []
    site.clock.advance(31 * 60)
    site.collector.poll()
    assert site.catalog.one("SELECT status FROM issues WHERE kind = 'COLLECTION_STUCK'")["status"] == "open"
    site.collector.exclusive_open = lambda p: True
    assert site.collector.poll().closed == ["2026-09-24"]
    assert site.catalog.one("SELECT status FROM issues WHERE kind = 'COLLECTION_STUCK'")["status"] == "resolved"


def test_a_session_end_marker_closes_the_night_standalone(site):
    light(site, 1)
    settle(site)
    marker = site.rig / "_altair" / "session-end-20260925T054000.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"host": "rig-esprit", "at": "2026-09-25T12:40:00Z", "night": "2026-09-24"}))
    site.collector.poll()   # sees the marker -> closing
    assert site.catalog.one("SELECT state, closed_by FROM collections")["closed_by"] == "session_end_marker"
    site.collector.poll()
    assert site.catalog.one("SELECT state FROM collections")["state"] == "closed"
    assert not site.catalog.one("SELECT 1 FROM rig_files WHERE rel_path LIKE '_altair/%'")


def test_a_late_frame_recloses_the_night_with_a_new_manifest(site):
    light(site, 1)
    settle(site)
    nights.request_close(site.catalog, site.config, rig="esprit", night="2026-09-24", closed_by="session_end")
    site.collector.poll()
    light(site, 2)
    settle(site)
    manifests = sorted(p.name for p in (site.nas / "raw" / "esprit" / "_manifests").iterdir())
    assert manifests == ["2026-09-24.json", "2026-09-24.v2.json"]
    assert site.catalog.one("SELECT n_files, closed_by FROM collections")["n_files"] == 2
    assert site.catalog.one("SELECT count(*) AS n FROM plan_requests WHERE kind = 'night_ready'")["n"] == 2


def test_the_same_file_under_a_new_name_is_never_registered_twice(site):
    path = light(site, 1)
    settle(site)
    moved = site.rig / "moved" / "copy.fits"
    moved.parent.mkdir()
    moved.write_bytes(path.read_bytes())
    _, report = settle(site)
    assert (report.collected, report.already_known) == (0, 1)
    assert site.catalog.one("SELECT count(*) AS n FROM frames")["n"] == 1


def test_frames_missing_required_headers_are_kept_but_invalid(site):
    light(site, 1, FILTER=None, filter_=None)
    settle(site)
    frame = site.catalog.one("SELECT status, status_reason FROM frames")
    assert frame["status"] == "invalid" and "filter" in frame["status_reason"]
    assert site.catalog.one("SELECT status FROM issues WHERE kind = 'HEADER_INCOMPLETE'")["status"] == "open"
