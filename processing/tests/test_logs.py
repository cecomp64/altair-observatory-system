"""Structured logs (SPEC §13): context fields, the JSON file, `altair logs`."""
from __future__ import annotations

import json
import logging
import threading

import pytest
import yaml
from click.testing import CliRunner

from altair import logs
from altair.cli import main

from helpers import pipeline_config


@pytest.fixture
def configured(tmp_path):
    config = pipeline_config(tmp_path)
    path = logs.setup(config, console=False)
    yield config, path
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_altair", False)]:
        root.removeHandler(handler)
        handler.close()


def test_records_carry_their_context_as_json(configured):
    config, path = configured
    log = logging.getLogger("altair.test")
    with logs.log_context(worker="processing"):
        with logs.log_context(job_id=7, kind="NIGHT_STACK", rig="esprit", night="2026-09-24", filter=None):
            log.warning("stack %s", "slow")
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("failed")
    log.info("no context")
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert entries[0] == {**entries[0], "level": "WARNING", "logger": "altair.test", "msg": "stack slow", "worker": "processing",
                          "job_id": 7, "kind": "NIGHT_STACK", "rig": "esprit", "night": "2026-09-24"}
    assert "filter" not in entries[0] and entries[0]["ts"].endswith("Z")
    assert entries[1]["worker"] == "processing" and "job_id" not in entries[1] and "ValueError: boom" in entries[1]["exc"]
    assert "worker" not in entries[2]


def test_context_is_per_thread(configured):
    config, path = configured
    seen = {}

    def other():
        seen["ctx"] = logs.current_context()

    with logs.log_context(job_id=1):
        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert seen["ctx"] == {}


def test_altair_logs_filters(configured, tmp_path):
    config, path = configured
    log = logging.getLogger("altair.executor")
    for job, level in ((1, logging.INFO), (2, logging.ERROR), (3, logging.INFO)):
        with logs.log_context(job_id=job, night="2026-09-24" if job < 3 else "2026-09-25"):
            log.log(level, "job %s done", job)
    cfg_path = tmp_path / "altair.yaml"
    cfg_path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    run = lambda *a: CliRunner().invoke(main, ["--config", str(cfg_path), "logs", *a], catch_exceptions=False).output
    assert run("--job", "2").count("job 2 done") == 1 and "job 1" not in run("--job", "2")
    assert "job 2 done" in run("--level", "error") and "job 1" not in run("--level", "error")
    assert [json.loads(line)["job_id"] for line in run("--night", "2026-09-24", "--json").splitlines()] == [1, 2]
    assert "job 3 done  [night=2026-09-25 job_id=3]" in run("--grep", "JOB 3")


def test_follow_prints_new_entries(configured):
    config, path = configured
    log = logging.getLogger("altair.test")
    log.info("before")
    got = []
    stop = threading.Event()
    reader = threading.Thread(target=lambda: got.extend(logs.follow(path, poll_s=0.05, stop=stop.is_set, level="WARNING")))
    reader.start()
    import time

    time.sleep(0.2)
    log.info("ignored")
    log.warning("after")
    for _ in range(40):
        if got:
            break
        time.sleep(0.05)
    stop.set()
    reader.join(2)
    assert [e["msg"] for e in got] == ["after"]
