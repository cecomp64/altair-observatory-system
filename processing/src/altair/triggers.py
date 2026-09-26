"""When a night closes without a session-end signal (SPEC §6.1):

- **Quiescence:** no new frame from a rig for ``quiescence_minutes``, and
  (with ``require_after_dawn``) the Sun is up past civil dawn on the morning
  after the night. The night closes with a ``SESSION_END_MARKER_MISSING``
  warning (the NINA end-of-sequence step didn't run).
- **Scheduled fallback:** once a day at ``scheduled_fallback_local``, every
  night still open from before today closes, provided its rig is reachable.
  A night whose rig is unreachable stays open; ``RIG_UNREACHABLE`` says why.

The Hub's ``night_ready`` command and the session-end marker are handled by
the command runner and the collector.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from altair import astro, nights
from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.ingest.headers import night_of
from altair.issues import raise_issue, resolve_issue


class Triggers:
    def __init__(self, catalog: Catalog, config: AltairConfig, *, clock: Callable[[], datetime] | None = None):
        self.catalog = catalog
        self.config = config
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def tick(self) -> list[tuple[str, str, str]]:
        """Closes what is due; returns (rig, night, closed_by) for each."""
        now = self.clock()
        closed = self._quiescence(now)
        closed += self._scheduled(now)
        self._clear_marker_warnings()
        return closed

    def current_night(self, now: datetime) -> str:
        return str(night_of(now, self.config.site.timezone, self.config.site.session_rollover_local))

    def _quiescence(self, now: datetime) -> list[tuple[str, str, str]]:
        site = self.config.site
        out = []
        for row in self.catalog.query("SELECT * FROM collections WHERE state = 'open' AND last_frame_at IS NOT NULL"):
            rig = self.config.rigs.get(row["rig"])
            if rig is None:
                continue
            quiet_since = datetime.fromisoformat(row["last_frame_at"].replace("Z", "+00:00"))
            if now - quiet_since < timedelta(minutes=rig.quiescence_minutes):
                continue
            if self.config.triggers.require_after_dawn and not astro.after_dawn(now, row["night"], site.timezone, site.latitude, site.longitude):
                continue
            if not self._reachable(row["rig"]):
                continue   # quiet because we can't see it: frames may still be waiting on the rig
            nights.request_close(self.catalog, self.config, rig=row["rig"], night=row["night"], closed_by="quiescence")
            with self.catalog.transaction() as tx:
                raise_issue(tx, self.config, kind="SESSION_END_MARKER_MISSING", severity="warning",
                            fingerprint=f"SESSION_END_MARKER_MISSING:{row['rig']}",
                            message=f"Night {row['night']} on {row['rig']} closed by quiescence: no session-end signal arrived. "
                                    "Check that NINA's end-of-sequence step runs `robs end-of-night` (or the session-end script).",
                            scope={"rig": row["rig"], "night": row["night"]})
            out.append((row["rig"], row["night"], "quiescence"))
        return out

    def _scheduled(self, now: datetime) -> list[tuple[str, str, str]]:
        tz = ZoneInfo(self.config.site.timezone)
        local = now.astimezone(tz)
        hours, minutes = (int(x) for x in self.config.triggers.scheduled_fallback_local.split(":"))
        due = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        key = "scheduled_fallback_last_run"
        if local < due or self.catalog.get_state(key) == local.date().isoformat():
            return []
        self.catalog.set_state(key, local.date().isoformat())
        # Nights that started before today's local date: at a morning fallback
        # that is last night; a night starting this evening is never touched.
        out = []
        for row in self.catalog.query("SELECT * FROM collections WHERE state = 'open' AND night < ?", (local.date().isoformat(),)):
            if not self._reachable(row["rig"]):
                continue   # RIG_UNREACHABLE explains why it stays open
            nights.request_close(self.catalog, self.config, rig=row["rig"], night=row["night"], closed_by="scheduled")
            out.append((row["rig"], row["night"], "scheduled"))
        return out

    def _reachable(self, rig: str) -> bool:
        location = self.catalog.one("SELECT reachable FROM locations WHERE name = ?", (f"rig:{rig}",))
        return location is None or bool(location["reachable"])

    def _clear_marker_warnings(self) -> None:
        """A rig whose latest closed night had a session-end signal again is fine."""
        with self.catalog.transaction() as tx:
            for issue in tx.execute("SELECT fingerprint FROM issues WHERE kind = 'SESSION_END_MARKER_MISSING' AND status = 'open'").fetchall():
                rig = issue["fingerprint"].split(":", 1)[1]
                latest = tx.execute("SELECT closed_by FROM collections WHERE rig = ? AND state = 'closed' ORDER BY night DESC LIMIT 1",
                                    (rig,)).fetchone()
                if latest and latest["closed_by"] in nights.SESSION_END:
                    resolve_issue(tx, self.config, issue["fingerprint"], resolution="auto:session_end_seen")
