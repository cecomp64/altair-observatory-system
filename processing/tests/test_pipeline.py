"""A whole night through the pipeline with a fake PixInsight: plan →
calibration masters → reference → night stack → merge → a second night →
a merge of both, and the Hub reports along the way."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from altair.catalog.db import Catalog
from altair.executor.executor import Executor
from altair.planner.plan import Planner
from altair.publish.publisher import Publisher

from helpers import add_frame, fake_pixinsight, make_sync, pipeline_config, set_fake_control

NIGHT1, NIGHT2 = "2026-09-24", "2026-09-26"


def night(catalog, config, night, *, lights=6, calib=True, date="2026-09-25"):
    nas = config.storage.nas.root
    shas = []
    for i in range(lights):
        shas.append(add_frame(catalog, night=night, date_obs=f"{date}T06:{i:02d}:00Z", nas_root=nas)[1])
    if calib:
        for i in range(3):
            add_frame(catalog, "dark", night=night, date_obs=f"{date}T12:0{i}:00Z", nas_root=nas)
            add_frame(catalog, "darkflat", night=night, exposure=2.5, date_obs=f"{date}T12:1{i}:00Z", nas_root=nas)
            add_frame(catalog, "flat", night=night, exposure=2.5, date_obs=f"{date}T12:2{i}:00Z", nas_root=nas)
    return shas


@pytest.fixture
def setup(tmp_path):
    def make(control=None, hub=False):
        config = pipeline_config(tmp_path, hub=hub, pixinsight=fake_pixinsight(tmp_path, control),
                                 paths={"published": str(tmp_path / "published")})
        catalog = Catalog(config.catalog_path)
        return config, catalog
    return make


def run(catalog, config, **kw):
    return Executor(catalog, config, **kw).run_all()


def test_a_night_end_to_end(setup, tmp_path):
    config, catalog = setup()
    lights = night(catalog, config, NIGHT1)
    plan = Planner(catalog, config).plan_night("esprit", NIGHT1)
    assert [j["plan"]["master"]["kind"] for j in plan.calib_jobs] == ["DARKFLAT", "DARK", "FLAT"]
    assert len(plan.reference_jobs) == 1 and plan.stacks[0]["plan"]["stack_kind"] == "final"

    reports = run(catalog, config)
    assert [(r.kind, r.status) for r in reports] == [
        ("CALIB_MASTER", "succeeded"), ("CALIB_MASTER", "succeeded"), ("CALIB_MASTER", "succeeded"),
        ("PROJECT_REFERENCE", "succeeded"), ("NIGHT_STACK", "succeeded"), ("MERGE", "succeeded")]

    masters = catalog.query("SELECT * FROM calibration_masters ORDER BY id")
    assert [m["kind"] for m in masters] == ["DARKFLAT", "DARK", "FLAT"] and all(m["job_id"] for m in masters)
    project = catalog.one("SELECT * FROM projects")
    assert project["reference_sha256"] and project["reference_night"] == NIGHT1
    nm = catalog.one("SELECT * FROM night_masters")
    assert nm["kind"] == "final" and nm["flat_verified"] == 1 and nm["merge_status"] == "merged" and nm["n_frames"] == 6
    assert json.loads(nm["input_frames_json"]) == sorted(lights)
    assert len(json.loads(nm["calib_json"])["calibrated"]) == 6
    mn = catalog.one("SELECT * FROM multi_night_masters")
    assert mn["version"] == 1 and mn["n_nights"] == 1
    # Frames: lights processed, calibration subs processed.
    assert {r["status"] for r in catalog.query("SELECT status FROM frames")} == {"processed"}

    # Every output is on the NAS (verified) and in the cache, under a hashed logical path.
    for sha in (nm["sha256"], mn["sha256"], project["reference_sha256"], masters[0]["sha256"]):
        blob = catalog.one("SELECT * FROM blobs WHERE sha256 = ?", (sha,))
        assert sha[:8] in blob["logical_path"]
        reps = {r["location"]: r for r in catalog.query("SELECT * FROM replicas WHERE sha256 = ?", (sha,))}
        assert reps["nas"]["state"] == "present" and Path(reps["nas"]["uri"]).exists()
        assert reps["cache"]["state"] == "present"
    assert catalog.one("SELECT data_class FROM blobs WHERE sha256 = ?", (nm["sha256"],))["data_class"] == "night_master"
    assert catalog.one("SELECT count(*) AS n FROM blobs WHERE data_class = 'calibrated_frame'")["n"] == 6
    # Sidecars: 3 calibration masters, the reference, the night, the merge.
    assert catalog.one("SELECT count(*) AS n FROM blobs WHERE data_class = 'metadata'")["n"] == 6
    published = list((tmp_path / "published").rglob("*.fits"))
    assert any("6x300s" in p.name for p in published) and any("_v001" in p.name for p in published)
    # Work directories of succeeded jobs are gone.
    assert not any(config.paths.work_dir.iterdir())

    # Planning the same night again adds nothing: every plan already succeeded.
    jobs_before = catalog.one("SELECT count(*) AS n FROM jobs")["n"]
    Planner(catalog, config).plan_night("esprit", NIGHT1)
    assert catalog.one("SELECT count(*) AS n FROM jobs")["n"] == jobs_before
    assert run(catalog, config) == []


def test_second_night_uses_the_library_and_merges_both(setup):
    config, catalog = setup()
    night(catalog, config, NIGHT1)
    Planner(catalog, config).plan_night("esprit", NIGHT1)
    run(catalog, config)
    night(catalog, config, NIGHT2, lights=8, calib=False, date="2026-09-27")
    plan = Planner(catalog, config).plan_night("esprit", NIGHT2)
    assert plan.calib_jobs == [] and plan.reference_jobs == [] and plan.issues == []
    reports = run(catalog, config)
    assert [(r.kind, r.status) for r in reports] == [("NIGHT_STACK", "succeeded"), ("MERGE", "succeeded")]
    v2 = catalog.one("SELECT * FROM multi_night_masters WHERE version = 2")
    inputs = json.loads(v2["inputs_json"])
    assert [i["night"] for i in inputs] == [NIGHT1, NIGHT2]
    # measured_psf_signal from the fake: the weight is the frame count.
    assert [round(i["percent"]) for i in inputs] == [43, 57]
    assert v2["total_exposure_s"] == 14 * 300


def test_gates_block_a_poor_night_until_it_is_included(setup, tmp_path):
    config, catalog = setup()
    night(catalog, config, NIGHT1)
    Planner(catalog, config).plan_night("esprit", NIGHT1)
    run(catalog, config)
    night(catalog, config, NIGHT2, lights=6, calib=False, date="2026-09-27")
    set_fake_control(tmp_path, {"fwhm": {NIGHT2: 20.0}})
    Planner(catalog, config).plan_night("esprit", NIGHT2)
    reports = run(catalog, config)
    assert [r.kind for r in reports] == ["NIGHT_STACK"]      # nothing new to merge
    poor = catalog.one("SELECT * FROM night_masters WHERE night = ?", (NIGHT2,))
    assert poor["merge_status"] == "blocked" and "FWHM" in poor["merge_block_reason"]
    issue = catalog.one("SELECT * FROM issues WHERE kind = 'QUALITY_OUTLIER'")
    assert issue["status"] == "open" and issue["severity"] == "warning"

    project = catalog.one("SELECT * FROM projects")
    with catalog.transaction() as tx:
        tx.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES ('night_include', ?, 'cli', '2026-09-27T00:00:00Z')",
                   (json.dumps({"target_id": project["hub_target_id"], "night": NIGHT2, "filter": "Ha"}),))
    Planner(catalog, config).process_requests()
    assert catalog.one("SELECT status FROM issues WHERE kind = 'QUALITY_OUTLIER'")["status"] == "resolved"
    reports = run(catalog, config)
    assert [r.kind for r in reports] == ["MERGE"]
    assert catalog.one("SELECT n_nights FROM multi_night_masters WHERE version = 2")["n_nights"] == 2


def test_failures_retry_then_raise_job_failed(setup, tmp_path):
    config, catalog = setup({"fail": "ImageIntegration failed"})
    night(catalog, config, NIGHT1)
    Planner(catalog, config).plan_night("esprit", NIGHT1)
    from helpers import Clock

    clock = Clock()
    executor = Executor(catalog, config, clock=clock)
    first = executor.run_once()
    assert first.status == "queued" and "ImageIntegration failed" in first.detail
    second = executor.run_once()
    assert second is None or second.job_id != first.job_id   # the first job is backing off
    for _ in range(config.pixinsight.max_attempts):
        clock.advance(3600 * 4)
        job = catalog.one("SELECT * FROM jobs WHERE id = ?", (first.job_id,))
        executor.execute(job)
    job = catalog.one("SELECT * FROM jobs WHERE id = ?", (first.job_id,))
    assert job["status"] == "failed"
    issue = catalog.one("SELECT * FROM issues WHERE fingerprint = ?", (f"JOB_FAILED:{first.job_id}",))
    assert issue["status"] == "open" and (config.paths.work_dir / str(first.job_id)).exists()
    # Dependants are blocked, and run once the dependency succeeds after a rerun.
    set_fake_control(tmp_path, {})
    executor.candidates()
    assert catalog.one("SELECT count(*) AS n FROM jobs WHERE status = 'blocked'")["n"] >= 1
    assert executor.rerun(first.job_id)
    reports = executor.run_all()
    assert catalog.one("SELECT count(*) AS n FROM jobs WHERE status != 'succeeded'")["n"] == 0, reports
    assert catalog.one("SELECT status FROM issues WHERE fingerprint = ?", (f"JOB_FAILED:{first.job_id}",))["status"] == "resolved"


@pytest.mark.parametrize("control, message", [({"no_result": True}, "no result.json"), ({"exit": 3}, "exit code 3")])
def test_a_run_without_result_or_with_an_error_exit_fails(setup, control, message):
    config, catalog = setup(control)
    night(catalog, config, NIGHT1)
    Planner(catalog, config).plan_night("esprit", NIGHT1)
    report = Executor(catalog, config).run_once()
    assert report.status == "queued" and message in report.detail


def test_crash_during_publishing_publishes_again_without_rerunning(setup, tmp_path):
    calls = tmp_path / "calls.jsonl"
    config, catalog = setup({"calls": str(calls)})
    night(catalog, config, NIGHT1)
    Planner(catalog, config).plan_night("esprit", NIGHT1)

    class Crash(Exception):
        pass

    class CrashingPublisher(Publisher):
        def publish(self, job, result, work_dir):
            raise Crash()

    executor = Executor(catalog, config, publisher=CrashingPublisher(catalog, config))
    with pytest.raises(Crash):
        executor.run_once()
    job = catalog.one("SELECT * FROM jobs WHERE status = 'running'")
    assert catalog.one("SELECT * FROM publish_intents WHERE job_id = ?", (job["id"],))
    assert Executor(catalog, config).recover() == [job["id"]]
    assert catalog.one("SELECT status FROM jobs WHERE id = ?", (job["id"],))["status"] == "succeeded"
    assert len(calls.read_text().splitlines()) == 1


def test_missing_flats_give_a_provisional_master_and_a_new_flat_resolves_it(setup):
    config, catalog = setup()
    nas = config.storage.nas.root
    for i in range(6):
        add_frame(catalog, night=NIGHT1, date_obs=f"2026-09-25T06:{i:02d}:00Z", nas_root=nas)
    for i in range(3):
        add_frame(catalog, "dark", night=NIGHT1, date_obs=f"2026-09-25T12:0{i}:00Z", nas_root=nas)
    plan = Planner(catalog, config).plan_night("esprit", NIGHT1)
    assert plan.stacks[0]["plan"]["stack_kind"] == "provisional_noflat"
    run(catalog, config)
    nm = catalog.one("SELECT * FROM night_masters")
    assert nm["kind"] == "provisional_noflat" and nm["merge_status"] == "provisional"
    assert "nas" not in {r["location"] for r in catalog.query("SELECT location FROM replicas WHERE sha256 = ?", (nm["sha256"],))}
    assert catalog.one("SELECT count(*) AS n FROM multi_night_masters")["n"] == 0
    assert {r["status"] for r in catalog.query("SELECT status FROM frames WHERE image_type = 'light'")} == {"held"}
    flat_issue = catalog.one("SELECT * FROM issues WHERE kind = 'FLAT_MISSING'")
    assert flat_issue["status"] == "open"

    # Flats (and dark-flats) arrive the next day; their masters resolve the issue automatically.
    for i in range(3):
        add_frame(catalog, "darkflat", night=NIGHT1, exposure=2.5, date_obs=f"2026-09-25T13:1{i}:00Z", nas_root=nas)
        add_frame(catalog, "flat", night=NIGHT1, exposure=2.5, date_obs=f"2026-09-25T13:2{i}:00Z", nas_root=nas)
    Planner(catalog, config).plan_night("esprit", NIGHT1)
    run(catalog, config)
    Planner(catalog, config).process_requests()
    run(catalog, config)
    assert catalog.one("SELECT status FROM issues WHERE kind = 'FLAT_MISSING'")["status"] == "resolved"
    final = catalog.one("SELECT * FROM night_masters WHERE kind = 'final'")
    assert final["merge_status"] == "merged"
    assert catalog.one("SELECT superseded_by FROM night_masters WHERE id = ?", (nm["id"],))["superseded_by"] == final["id"]
    assert {r["status"] for r in catalog.query("SELECT status FROM frames WHERE image_type = 'light'")} == {"processed"}


def test_hub_reports_products_jobs_and_masters(tmp_path, fake_hub):
    config = pipeline_config(tmp_path, hub=True, pixinsight=fake_pixinsight(tmp_path))
    catalog = Catalog(config.catalog_path)
    night(catalog, config, NIGHT1)
    Planner(catalog, config).plan_night("esprit", NIGHT1)
    run(catalog, config)
    sync = make_sync(catalog, config, fake_hub)
    sync.drainer.drain()
    assert fake_hub.contract_violations == []
    kinds = {k for k, _ in fake_hub.products}
    assert kinds == {"night_master", "project_reference", "multi_night_master"}
    night_product = next(v for (k, _), v in fake_hub.products.items() if k == "night_master")
    assert night_product["metadata"]["archive_uri"] is None and night_product["metadata"]["target_id"] == 34
    assert night_product["metadata"]["metrics"]["frames"] == 6 and len(night_product["metadata"]["metrics"]["frame_sha256s"]) == 6
    assert set(night_product["files"]) == {"preview", "thumbnail"}
    assert len(fake_hub.masters) == 3
    assert {j["status"] for j in fake_hub.jobs.values()} == {"succeeded"}
