"""What gets sent, and when (SPEC §10.2, §10.3):

- a message for each newly opened blocking or warning issue (``issue_opened``);
  ``info`` issues only show on the status page, except RESTORE_IN_PROGRESS;
- a message when an issue is resolved automatically (``issue_resolved``);
- a reminder for open blocking issues every ``remind_every_days``;
- one summary per closed rig-night once its jobs are done
  (``success`` / ``partial`` / ``failure``).

Every message has a key in ``notifications_sent``, so each is sent once per
channel; a channel that fails is retried on the next tick.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.notify.channels import CHANNELS, ChannelError, Message

log = logging.getLogger("altair.notify")
NOTIFY_INFO = {"RESTORE_IN_PROGRESS"}


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class Notifier:
    def __init__(self, catalog: Catalog, config: AltairConfig, *, clock: Callable[[], datetime] | None = None,
                 channels: dict[str, Callable] | None = None):
        self.catalog = catalog
        self.config = config
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.channels = channels or CHANNELS
        self.page = config.issues.page

    # ── sending ──────────────────────────────────────────────────────────
    def _sent(self, key: str, channel: str) -> bool:
        return self.catalog.one("SELECT 1 FROM notifications_sent WHERE key = ? AND channel = ?", (key, channel)) is not None

    def send(self, key: str, msg: Message) -> int:
        """Send on every configured channel that hasn't had this message yet."""
        delivered = 0
        for i, cfg in enumerate(self.config.notifications.channels):
            kind = cfg.get("type")
            channel = f"{i}:{kind}"
            if kind not in self.channels or self._sent(key, channel):
                continue
            try:
                self.channels[kind](cfg, msg)
            except (ChannelError, OSError) as exc:
                log.warning("notification %s via %s failed: %s", key, kind, exc)
                continue
            self.catalog.execute("INSERT OR IGNORE INTO notifications_sent(key, channel, sent_at) VALUES (?, ?, ?)", (key, channel, now_iso()))
            delivered += 1
        return delivered

    # ── the tick ─────────────────────────────────────────────────────────
    def tick(self) -> dict:
        on = set(self.config.notifications.on)
        out = {"opened": 0, "resolved": 0, "reminders": 0, "nights": 0}
        if "issue_opened" in on:
            out["opened"] = self._opened()
        if "issue_resolved" in on:
            out["resolved"] = self._resolved()
        out["reminders"] = self._reminders()
        if on & {"success", "partial", "failure"}:
            out["nights"] = self._nights(on)
        return out

    def _opened(self) -> int:
        count = 0
        for issue in self.catalog.query("SELECT * FROM issues WHERE status = 'open'"):
            if issue["severity"] == "info" and issue["kind"] not in NOTIFY_INFO:
                continue
            key = f"open:{issue['id']}:{issue['created_at']}:{issue['severity']}"
            if self._all_sent(key):
                continue
            count += bool(self.send(key, self.issue_message(issue)))
            self.catalog.execute("UPDATE issues SET last_notified_at = ? WHERE id = ?", (now_iso(), issue["id"]))
        return count

    def _resolved(self) -> int:
        count = 0
        since = (self.clock() - timedelta(days=7)).isoformat().replace("+00:00", "Z")
        for issue in self.catalog.query("SELECT * FROM issues WHERE status = 'resolved' AND resolved_at >= ? AND resolution LIKE 'auto%'", (since,)):
            if issue["severity"] == "info" and issue["kind"] not in NOTIFY_INFO:
                continue
            key = f"resolved:{issue['id']}:{issue['resolved_at']}"
            if not self._all_sent(key) and self._was_announced(issue):
                count += bool(self.send(key, Message(f"✓ Altair: {issue['kind']} resolved (issue #{issue['id']})",
                                                     f"{issue['message']}\n\nResolved: {issue['resolution']}.", url=self.page)))
        return count

    def _reminders(self) -> int:
        count = 0
        every = timedelta(days=self.config.issues.remind_every_days)
        now = self.clock()
        for issue in self.catalog.query("SELECT * FROM issues WHERE status = 'open' AND severity = 'blocking'"):
            last = _ts(issue["last_notified_at"])
            if last is None or now - last < every:
                continue
            key = f"remind:{issue['id']}:{now.date().isoformat()}"
            msg = self.issue_message(issue)
            msg.title = "Reminder: " + msg.title
            count += bool(self.send(key, msg))
            self.catalog.execute("UPDATE issues SET last_notified_at = ? WHERE id = ?", (now_iso(), issue["id"]))
        return count

    def _nights(self, on: set[str]) -> int:
        count = 0
        for night in self.catalog.query("SELECT * FROM collections WHERE state = 'closed' ORDER BY night DESC LIMIT 30"):
            jobs = self.catalog.query("SELECT * FROM jobs WHERE rig = ? AND night = ?", (night["rig"], night["night"]))
            if not jobs or any(j["status"] in ("queued", "staging", "waiting_data", "running") for j in jobs):
                continue
            key = f"night:{night['rig']}:{night['night']}:{max(j['finished_at'] or '' for j in jobs)}"
            if self._all_sent(key):
                continue
            failed = [j for j in jobs if j["status"] in ("failed", "blocked")]
            stacks = [j for j in jobs if j["kind"] == "NIGHT_STACK" and j["status"] == "succeeded"]
            outcome = "failure" if failed and not stacks else "partial" if failed or self._open_for(night) else "success"
            if outcome not in on:
                continue
            count += bool(self.send(key, self.night_message(night, jobs, outcome)))
        return count

    def _all_sent(self, key: str) -> bool:
        wanted = {f"{i}:{c.get('type')}" for i, c in enumerate(self.config.notifications.channels) if c.get("type") in self.channels}
        sent = {r["channel"] for r in self.catalog.query("SELECT channel FROM notifications_sent WHERE key = ?", (key,))}
        return wanted <= sent

    def _was_announced(self, issue) -> bool:
        return self.catalog.one("SELECT 1 FROM notifications_sent WHERE key LIKE ?", (f"open:{issue['id']}:%",)) is not None

    def _open_for(self, night) -> list:
        return [i for i in self.catalog.query("SELECT scope_json FROM issues WHERE status = 'open' AND severity = 'blocking'")
                if (s := json.loads(i["scope_json"])).get("rig") == night["rig"] and s.get("night") == night["night"]]

    # ── message text (§10.2) ─────────────────────────────────────────────
    def issue_message(self, issue) -> Message:
        blocks = {"FLAT_MISSING": "merge blocked", "ROTATOR_POSITION_UNKNOWN": "merge blocked", "DARK_MISSING": "night blocked",
                  "BIAS_MISSING": "night blocked", "DARKFLAT_MISSING": "night blocked", "JOB_FAILED": "job failed",
                  "DATA_AT_RISK": "backup at risk", "NAS_UNREACHABLE": "collection paused"}.get(issue["kind"], issue["severity"])
        icon = "⚠" if issue["severity"] == "blocking" else "ℹ"
        body = issue["message"]
        if issue["kind"] in ("FLAT_MISSING", "DARK_MISSING", "BIAS_MISSING", "DARKFLAT_MISSING"):
            body += f"\n(Manual: `altair calib import <file>` or `altair issue resolve {issue['id']} --flat <file>`.)"
        return Message(f"{icon} Altair — {issue['kind']} (issue #{issue['id']}) — {blocks}", body,
                       priority="high" if issue["severity"] == "blocking" else "normal", url=self.page)

    def night_message(self, night, jobs, outcome: str) -> Message:
        by_kind: dict[str, dict[str, int]] = {}
        for j in jobs:
            by_kind.setdefault(j["kind"], {}).setdefault(j["status"], 0)
            by_kind[j["kind"]][j["status"]] += 1
        lines = [f"{kind}: " + ", ".join(f"{n} {status}" for status, n in sorted(s.items())) for kind, s in sorted(by_kind.items())]
        masters = self.catalog.query(
            "SELECT n.filter, n.kind, n.n_frames, n.total_exposure_s, n.merge_status, p.target, p.hub_target_id FROM night_masters n "
            "JOIN projects p ON p.id = n.project_id JOIN jobs j ON j.id = n.job_id WHERE j.rig = ? AND j.night = ? AND n.superseded_by IS NULL",
            (night["rig"], night["night"]))
        for m in masters:
            lines.append(f"{m['target']} {m['filter']}: {m['n_frames']} frames, {(m['total_exposure_s'] or 0) / 3600:.1f} h "
                         f"({m['kind']}, {m['merge_status']})")
        for issue in self._open_for(night):
            lines.append(f"open issue: {json.loads(issue['scope_json']).get('filter') or ''}")
        icon = {"success": "✓", "partial": "◐", "failure": "✗"}[outcome]
        return Message(f"{icon} Altair — night {night['night']} on {night['rig']}: {outcome}", "\n".join(lines),
                       priority="high" if outcome == "failure" else "normal", url=self.page)
