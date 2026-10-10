"""robs target-start: the target Target Scheduler just started, from its log,
reported to the Hub as a target_start session event."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import responses
from click.testing import CliRunner

from robs import ts_log
from robs.cli import main
from robs.hub import Hub
from robs.sync import sync_targets_into_scheduler
from test_hub_integration import BASE, contract_errors, response, target, write_config

# The plugin's log format (TSPublisher.cs), with a wait before the target.
LOG = """\
2026-10-10T19:40:22.5081|INFO|TargetSchedulerContainer.cs|Execute|314|waiting for next target to become available: 2026-10-10 19:59:44 -07:00
2026-10-10T19:40:22.5082|DEBUG|TSPublisher.cs|Publish|29|publishing message to 'TargetScheduler-WaitStart', message:
    content: 10/10/2026 19:59:44
    headers:
        TargetName: #34 M31
2026-10-10T20:01:13.1000|DEBUG|TSPublisher.cs|Publish|29|publishing message to 'TargetScheduler-NewTargetStart', message:
    content: {name}
    id: d2e20471-a0a1-40cd-8457-8d37e625140b
    headers:
        ProjectName: #P12 Project 12
2026-10-10T20:01:13.2000|DEBUG|TSPublisher.cs|Publish|29|publishing message to 'TargetScheduler-TargetStart', message:
    content: {name}
"""
NOW = datetime(2026, 10, 10, 20, 3)


def write_log(config, name: str = "#34 M31", file_name: str = "TS-20261010-193000-3.2.0.9001.1.log") -> None:
    logs = config.scheduler_db_path.parent / "Logs"
    logs.mkdir(exist_ok=True)
    (logs / file_name).write_text(LOG.replace("{name}", name), encoding="utf-8")


def test_reads_the_last_started_target_from_the_newest_log(tmp_path, scheduler_db_path):
    config = write_config(tmp_path, scheduler_db_path)
    assert ts_log.last_started_target(config, now=NOW) is None  # no log yet

    write_log(config)

    assert ts_log.last_started_target(config, now=NOW) == "#34 M31"
    assert ts_log.last_started_target(config, now=NOW + timedelta(hours=1)) is None  # an earlier target's start


def test_hub_target_id_comes_from_the_nina_name():
    assert ts_log.hub_target_id("#34 M31") == 34
    assert ts_log.hub_target_id("M 31") is None


@responses.activate
def test_target_start_reports_the_started_target_to_the_hub(tmp_path, scheduler_db_path, monkeypatch):
    config = write_config(tmp_path, scheduler_db_path)
    responses.get(f"{BASE}/telescopes/test-scope/active_targets", json=response(target(34, 12)))
    sync_targets_into_scheduler(config, Hub.for_config(config))
    session = responses.post(f"{BASE}/telescopes/test-scope/sessions", json={"ok": True}, status=201)
    responses.post(f"{BASE}/heartbeat", json={"ok": True, "api_revision": 5})
    write_log(config)
    monkeypatch.setattr(ts_log, "MAX_AGE", timedelta(days=36500))

    result = CliRunner().invoke(main, ["target-start", "--config", str(tmp_path / "t.yml")])

    assert result.exit_code == 0 and "Reported #34 M31 as started" in result.output, result.output
    body = json.loads(session.calls[0].request.body)
    assert contract_errors("worker/session_event.request.json", body) == []
    assert body["event"] == "target_start" and body["target_ids"] == [34]


@responses.activate
def test_target_start_reports_nothing_for_a_target_the_hub_does_not_manage(tmp_path, scheduler_db_path, monkeypatch):
    config = write_config(tmp_path, scheduler_db_path)
    session = responses.post(f"{BASE}/telescopes/test-scope/sessions", json={"ok": True}, status=201)
    responses.post(f"{BASE}/heartbeat", json={"ok": True, "api_revision": 5})
    write_log(config, name="M 31")
    monkeypatch.setattr(ts_log, "MAX_AGE", timedelta(days=36500))

    result = CliRunner().invoke(main, ["target-start", "--config", str(tmp_path / "t.yml")])

    assert result.exit_code == 0 and "Nothing reported" in result.output, result.output
    assert session.call_count == 0


@responses.activate
def test_target_start_says_so_when_an_older_hub_rejects_the_event(tmp_path, scheduler_db_path, monkeypatch):
    config = write_config(tmp_path, scheduler_db_path)
    responses.get(f"{BASE}/telescopes/test-scope/active_targets", json=response(target(34, 12)))
    sync_targets_into_scheduler(config, Hub.for_config(config))
    responses.post(f"{BASE}/telescopes/test-scope/sessions", json={"error": "event must be one of roof_open, roof_close, session_end"}, status=422)
    responses.post(f"{BASE}/heartbeat", json={"ok": True, "api_revision": 4})
    write_log(config)
    monkeypatch.setattr(ts_log, "MAX_AGE", timedelta(days=36500))

    result = CliRunner().invoke(main, ["target-start", "--config", str(tmp_path / "t.yml")])

    assert result.exit_code == 0 and "The Hub did not accept" in result.output, result.output
