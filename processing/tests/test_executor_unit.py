"""The executor's parts in isolation: the PixInsight command line and
timeout, the job.json/result.json contract, night weights and merge gates."""
from __future__ import annotations

import json
import sys

import pytest

from altair.config import PixInsight
from altair.executor import pixinsight
from altair.executor.contract import ContractError, read_result, write_job
from altair.projects import weights


def test_command_line_follows_the_spec(tmp_path):
    cfg = PixInsight(executable="C:/PI/PixInsight.exe", runner="C:/Altair/pjsr/altair_runner.js", instance_slot=5)
    assert pixinsight.command(cfg, tmp_path / "job.json") == [
        "C:/PI/PixInsight.exe", "-n=5", "--automation-mode", "--no-startup-scripts", "--force-exit",
        f"-r=C:/Altair/pjsr/altair_runner.js,{tmp_path / 'job.json'}"]
    assert pixinsight.runner_path(PixInsight()).name == "altair_runner.js" and pixinsight.runner_path(PixInsight()).exists()


def test_a_run_past_its_timeout_is_killed_and_logged(tmp_path):
    log = tmp_path / "job.log"
    outcome = pixinsight.run([sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"], log, timeout_s=1)
    assert outcome.timed_out and outcome.seconds < 20
    assert "timeout after 1s" in log.read_text()
    ok = pixinsight.run([sys.executable, "-c", "print('hello')"], log, timeout_s=30)
    assert ok.returncode == 0 and not ok.timed_out and "hello" in log.read_text()
    missing = pixinsight.run([str(tmp_path / "nope.exe")], log, timeout_s=5)
    assert missing.returncode is None and "could not start" in log.read_text()


def test_contract_round_trip_and_errors(tmp_path):
    write_job(tmp_path / "job.json", {"kind": "NIGHT_STACK", "job_id": 1})
    assert json.loads((tmp_path / "job.json").read_text())["schema"] == 1
    with pytest.raises(ContractError, match="no result.json"):
        read_result(tmp_path)
    (tmp_path / "result.json").write_text("{nope")
    with pytest.raises(ContractError, match="not JSON"):
        read_result(tmp_path)
    (tmp_path / "result.json").write_text(json.dumps({"status": "ok", "outputs": [{"role": "master"}]}))
    with pytest.raises(ContractError, match="without role/path"):
        read_result(tmp_path)
    (tmp_path / "result.json").write_text(json.dumps({"status": "ok", "outputs": [{"role": "master", "path": "m.xisf"},
                                                                                  {"role": "calibrated_frame", "path": "a", "source_sha256": "x"}]}))
    result = read_result(tmp_path)
    assert result.output("master").path == "m.xisf" and [o.source_sha256 for o in result.outputs_of("calibrated_frame")] == ["x"]


NIGHTS = [{"night": "n1", "sha256": "a", "frame_weight_sum": 3.0}, {"night": "n2", "sha256": "b", "frame_weight_sum": 1.0}]
MEASURED = [{"sha256": "a", "psf_signal_weight": 0.2, "noise_sigma": 0.01, "scale": 1.0},
            {"sha256": "b", "psf_signal_weight": 0.1, "noise_sigma": 0.02, "scale": 0.5}]


def test_night_weights():
    assert weights.compute("measured_psf_signal", NIGHTS, MEASURED) == {"a": 0.2, "b": 0.1}
    ivn = weights.compute("inverse_noise_variance", NIGHTS, MEASURED)
    assert ivn["a"] == pytest.approx(10000) and ivn["b"] == pytest.approx(625)
    assert weights.compute("frame_weight_sum", NIGHTS, []) == {"a": 3.0, "b": 1.0}
    assert weights.percentages({"a": 3.0, "b": 1.0}) == {"a": 75.0, "b": 25.0}
    assert weights.normalization_reference({"a": 0.2, "b": 0.3}) == "b"
    with pytest.raises(weights.WeightError, match="not measured"):
        weights.compute("measured_psf_signal", NIGHTS, MEASURED[:1])
    with pytest.raises(weights.WeightError, match="no usable"):
        weights.compute("frame_weight_sum", [{"night": "n3", "sha256": "c", "frame_weight_sum": None}], [])


def test_merge_gates(tmp_path):
    from altair.catalog.db import Catalog
    from altair.projects.merge import evaluate

    from helpers import pipeline_config

    config = pipeline_config(tmp_path)
    catalog = Catalog(config.catalog_path)
    with catalog.transaction() as tx:
        tx.execute("INSERT INTO projects(id, target, telescope, camera, rig, reference_sha256, reference_version, drizzle_scale, path) "
                   "VALUES (1, 'M31', 'esprit100', 'asi2600mm', 'esprit', 'ref', 2, 1, 'projects/esprit/M31')")
        rows = [("n1", 2, 1, {"fwhm": 2.5, "overlap_fraction": 0.95}, 1), ("n2", 1, 1, {"fwhm": 2.5}, 1),
                ("n3", 2, 0, {"fwhm": 2.5}, 1), ("n4", 2, 1, {"fwhm": 2.5, "overlap_fraction": 0.5}, 1),
                ("n5", 2, 1, {"fwhm": 9.0}, 1), ("n6", 2, 1, {"fwhm": 2.6}, 2), ("n7", 2, 1, {"fwhm": 2.4}, 1)]
        for night, version, flat, metrics, drizzle in rows:
            tx.execute("INSERT INTO night_masters(project_id, night, filter, sha256, input_frames_json, kind, reference_version, flat_verified, "
                       "metrics_json, calib_json) VALUES (1, ?, 'Ha', ?, '[]', 'final', ?, ?, ?, ?)",
                       (night, f"sha-{night}", version, flat, json.dumps(metrics), json.dumps({"drizzle_scale": drizzle})))
        tx.execute("INSERT INTO night_decisions VALUES (1, 'n7', 'Ha', 'exclude', 'cli', '2026-09-26')")
    project = catalog.one("SELECT * FROM projects")
    gates = {g.night_master["night"]: g for g in evaluate(catalog, config, project, "Ha")}
    assert gates["n1"].eligible
    assert (gates["n2"].issue, gates["n3"].issue, gates["n4"].issue, gates["n5"].issue, gates["n6"].issue) == (
        "STALE_REFERENCE", "FLAT_MISSING", "LOW_OVERLAP", "QUALITY_OUTLIER", "SCALE_MISMATCH")
    assert gates["n5"].severity == "warning" and gates["n7"].reason == "excluded by the user"
