"""Notifications (SPEC §10.2, §10.3): what is sent, once, and the channels."""
from __future__ import annotations

import httpx
import pytest

from altair.catalog.db import Catalog
from altair.issues import raise_issue, resolve_issue
from altair.notify import channels
from altair.notify.channels import ChannelError, Message
from altair.notify.notifier import Notifier

from helpers import Clock, pipeline_config


@pytest.fixture
def env(tmp_path):
    config = pipeline_config(tmp_path, notifications={"channels": [{"type": "a"}, {"type": "b"}]}, issues={"remind_every_days": 3})
    catalog = Catalog(config.catalog_path)
    clock = Clock()
    sent = {"a": [], "b": []}
    failing = {"b": True}

    def b(cfg, msg):
        if failing["b"]:
            raise ChannelError("down")
        sent["b"].append(msg)

    notifier = Notifier(catalog, config, clock=clock, channels={"a": lambda cfg, msg: sent["a"].append(msg), "b": b})
    return config, catalog, clock, notifier, sent, failing


def issue(catalog, config, kind="FLAT_MISSING", severity="blocking", fp="FLAT_MISSING:esprit:Ha:1:1x1:2026-09-24"):
    with catalog.transaction() as tx:
        return raise_issue(tx, config, kind=kind, severity=severity, fingerprint=fp, message="Take Ha flats.",
                           scope={"rig": "esprit", "night": "2026-09-24", "filter": "Ha"})


def test_an_issue_is_announced_once_per_channel_and_a_failed_channel_retries(env):
    config, catalog, clock, notifier, sent, failing = env
    issue_id = issue(catalog, config)
    issue(catalog, config, kind="RESTORE_IN_PROGRESS", severity="info", fp="RESTORE_IN_PROGRESS:job:1")
    issue(catalog, config, kind="LOCATION_INFO", severity="info", fp="X")
    notifier.tick()
    assert [m.title for m in sent["a"]] == [f"⚠ Altair — FLAT_MISSING (issue #{issue_id}) — merge blocked",
                                            "ℹ Altair — RESTORE_IN_PROGRESS (issue #2) — info"]
    assert "altair issue resolve" in sent["a"][0].body and sent["a"][0].priority == "high"
    assert sent["b"] == []
    notifier.tick()
    assert len(sent["a"]) == 2 and sent["b"] == []      # a isn't repeated; b still down
    failing["b"] = False
    notifier.tick()
    assert len(sent["a"]) == 2 and len(sent["b"]) == 2


def test_reminders_and_resolution(env):
    config, catalog, clock, notifier, sent, failing = env
    failing["b"] = False
    issue_id = issue(catalog, config)
    notifier.tick()
    clock.advance(86400 * 2)
    notifier.tick()
    assert len(sent["a"]) == 1
    clock.advance(86400 * 1.5)
    notifier.tick()
    assert sent["a"][-1].title.startswith("Reminder: ")
    with catalog.transaction() as tx:
        resolve_issue(tx, config, "FLAT_MISSING:esprit:Ha:1:1x1:2026-09-24", resolution="auto:matched")
    notifier.tick()
    assert sent["a"][-1].title == f"✓ Altair: FLAT_MISSING resolved (issue #{issue_id})"
    notifier.tick()
    assert sum("resolved" in m.title for m in sent["a"]) == 1


def test_a_night_summary_after_its_jobs(env):
    config, catalog, clock, notifier, sent, failing = env
    with catalog.transaction() as tx:
        tx.execute("INSERT INTO collections(rig, night, state) VALUES ('esprit', '2026-09-24', 'closed')")
        tx.execute("INSERT INTO jobs(kind, scope_json, plan_json, plan_hash, status, rig, night, finished_at) VALUES "
                   "('NIGHT_STACK', '{}', '{}', 'h1', 'running', 'esprit', '2026-09-24', NULL)")
    notifier.tick()
    assert sent["a"] == []
    catalog.execute("UPDATE jobs SET status = 'failed', finished_at = '2026-09-25T09:00:00Z'")
    notifier.tick()
    assert sent["a"][-1].title == "✗ Altair — night 2026-09-24 on esprit: failure" and "NIGHT_STACK: 1 failed" in sent["a"][-1].body


def test_pushover_ntfy_email_and_toast(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("PO_TOKEN", "tok")
    monkeypatch.setenv("PO_USER", "usr")
    msg = Message("⚠ Altair — FLAT_MISSING", "Take flats", priority="high", url="file:///status.html")
    channels.pushover({"type": "pushover", "token_env": "PO_TOKEN", "user_env": "PO_USER"}, msg, client=client)
    assert seen[0].url == "https://api.pushover.net/1/messages.json" and b"priority=1" in seen[0].content
    channels.ntfy({"type": "ntfy", "topic": "altair-test"}, msg, client=client)
    assert str(seen[1].url) == "https://ntfy.sh/altair-test" and seen[1].headers["Priority"] == "high"
    with pytest.raises(ChannelError, match="MISSING"):
        channels.pushover({"type": "pushover", "token_env": "MISSING", "user_env": "PO_USER"}, msg, client=client)

    mails = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            mails.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            mails.append(("starttls",))

        def login(self, user, password):
            mails.append(("login", user, password))

        def send_message(self, mail):
            mails.append(("send", mail["To"], mail["Subject"]))

    monkeypatch.setenv("SMTP_URL", "smtp://me%40example.com:secret@mail.example.com:587")
    channels.email({"type": "email", "smtp_url_env": "SMTP_URL", "to": "me@example.com"}, msg, smtp_factory=FakeSMTP)
    assert mails == [("connect", "mail.example.com", 587), ("starttls",), ("login", "me@example.com", "secret"),
                     ("send", "me@example.com", "⚠ Altair — FLAT_MISSING")]

    calls = []
    channels.windows_toast({"type": "windows_toast", "force": True}, msg,
                           run=lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0, "stderr": b""})())
    assert calls[0][0] == "powershell" and "ToastNotificationManager" in calls[0][-1] and "FLAT_MISSING" in calls[0][-1]
