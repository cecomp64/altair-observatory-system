"""The Hub outbox (SPEC §17.3).

Items are written in the same transaction as the catalog change they
describe. The drainer sends them in id order, batching frames (<= 500) and
sending only the newest payload per natural key. Network errors and 5xx
retry forever with exponential back-off; a 4xx retries 5 times, then the
item is parked and HUB_REJECTED is raised.
"""
from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from altair.catalog.db import Catalog, now_iso
from altair.hub.client import HubClient, HubRejected, HubUnavailable

KINDS = ("frame", "frame_patch", "night", "calibration_master", "data_product", "issue", "job")
BASE_BACKOFF_S = 5.0
MAX_4XX_ATTEMPTS = 5


def enqueue(tx: sqlite3.Connection, kind: str, natural_key: str, payload: dict[str, Any], *,
            attachments: dict[str, str | None] | None = None, coalesce_merge: bool = False) -> None:
    assert kind in KINDS, kind
    if coalesce_merge:
        # Partial updates to the same key merge into the pending item.
        pending = tx.execute(
            "SELECT id, payload_json FROM hub_outbox WHERE kind = ? AND natural_key = ? AND sent_at IS NULL AND parked = 0 "
            "ORDER BY id DESC LIMIT 1", (kind, natural_key)).fetchone()
        if pending:
            merged = {**json.loads(pending["payload_json"]), **payload}
            tx.execute("UPDATE hub_outbox SET payload_json = ? WHERE id = ?", (json.dumps(merged), pending["id"]))
            return
    tx.execute(
        "INSERT INTO hub_outbox(kind, natural_key, payload_json, attachment_paths_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (kind, natural_key, json.dumps(payload), json.dumps({k: v for k, v in (attachments or {}).items() if v}) or None, now_iso()),
    )


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


class Drainer:
    def __init__(self, catalog: Catalog, client: HubClient, *, batch_size: int = 500, max_backoff_s: float = 900,
                 on_rejected: Callable[[sqlite3.Row, str], None] | None = None,
                 on_frame_result: Callable[[dict], None] | None = None, clock: Callable[[], datetime] | None = None):
        self.catalog = catalog
        self.client = client
        self.batch_size = batch_size
        self.max_backoff_s = max_backoff_s
        self.on_rejected = on_rejected
        self.on_frame_result = on_frame_result
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def depth(self) -> int:
        return self.catalog.one("SELECT count(*) AS n FROM hub_outbox WHERE sent_at IS NULL AND parked = 0")["n"]

    def parked(self) -> int:
        return self.catalog.one("SELECT count(*) AS n FROM hub_outbox WHERE sent_at IS NULL AND parked = 1")["n"]

    def due(self, limit: int = 5000) -> list[sqlite3.Row]:
        now = self.clock().isoformat()
        return self.catalog.query(
            "SELECT * FROM hub_outbox WHERE sent_at IS NULL AND parked = 0 "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) ORDER BY id LIMIT ?", (now, limit))

    def drain(self) -> dict[str, int]:
        """Send everything due. Stops at the first HubUnavailable (the Hub is
        down: the rest would fail the same way). Returns counts."""
        stats = {"sent": 0, "failed": 0, "parked": 0, "coalesced": 0}
        rows = self.due()
        # Newest payload per natural key; older duplicates are simply marked sent.
        latest: OrderedDict[tuple[str, str], sqlite3.Row] = OrderedDict()
        superseded = []
        for row in rows:
            key = (row["kind"], row["natural_key"])
            if key in latest:
                superseded.append(latest.pop(key)["id"])
            latest[key] = row
        if superseded:
            self._mark_sent(superseded)
            stats["coalesced"] = len(superseded)

        pending = list(latest.values())
        try:
            for kind in KINDS:
                items = [r for r in pending if r["kind"] == kind]
                if kind in ("frame", "frame_patch"):
                    for i in range(0, len(items), self.batch_size):
                        self._send_frames(kind, items[i:i + self.batch_size], stats)
                else:
                    for row in items:
                        self._send_one(row, stats)
        except HubUnavailable as exc:
            stats["unavailable"] = 1
            stats["error"] = str(exc)  # type: ignore[assignment]
        return stats

    # ── sending ──────────────────────────────────────────────────────────
    def _send_frames(self, kind: str, rows: list[sqlite3.Row], stats: dict) -> None:
        payloads = [json.loads(r["payload_json"]) for r in rows]
        try:
            results = self.client.post_frames(payloads) if kind == "frame" else self.client.patch_frames(payloads)
        except HubUnavailable:
            self._retry(rows, "Hub unavailable", rejected=False)
            stats["failed"] += len(rows)
            raise
        except HubRejected as exc:
            self._retry(rows, exc.message, rejected=True, stats=stats)
            return
        by_sha = {r["sha256"]: r for r in results}
        ok, bad = [], []
        for row in rows:
            result = by_sha.get(row["natural_key"])
            if result and result.get("status") == "ok":
                ok.append(row["id"])
                if kind == "frame" and self.on_frame_result:
                    self.on_frame_result(result)
            else:
                bad.append((row, (result or {}).get("error", "no result for this frame")))
        self._mark_sent(ok)
        stats["sent"] += len(ok)
        for row, error in bad:
            self._retry([row], error, rejected=True, stats=stats)

    def _send_one(self, row: sqlite3.Row, stats: dict) -> None:
        payload = json.loads(row["payload_json"])
        try:
            kind = row["kind"]
            if kind == "night":
                self.client.put_night(payload["optical_train"], payload["night"], payload["body"])
            elif kind == "calibration_master":
                self.client.put_calibration_master(payload["altair_id"], payload["body"])
            elif kind == "data_product":
                attachments = json.loads(row["attachment_paths_json"] or "{}")
                self.client.put_data_product(payload["kind"], payload["altair_id"], payload["metadata"], attachments)
            elif kind == "issue":
                self.client.put_issue(payload["fingerprint"], payload["body"])
            elif kind == "job":
                self.client.put_job(payload["altair_id"], payload["body"])
        except HubUnavailable:
            self._retry([row], "Hub unavailable", rejected=False)
            stats["failed"] += 1
            raise
        except HubRejected as exc:
            self._retry([row], exc.message, rejected=True, stats=stats)
            return
        self._mark_sent([row["id"]])
        stats["sent"] += 1

    # ── bookkeeping ──────────────────────────────────────────────────────
    def _mark_sent(self, ids: list[int]) -> None:
        if ids:
            marks = ",".join("?" * len(ids))
            self.catalog.execute(f"UPDATE hub_outbox SET sent_at = ?, last_error = NULL WHERE id IN ({marks})", (now_iso(), *ids))

    def _retry(self, rows: list[sqlite3.Row], error: str, *, rejected: bool, stats: dict | None = None) -> None:
        for row in rows:
            attempts = row["attempts"] + 1
            if rejected and attempts >= MAX_4XX_ATTEMPTS:
                self.catalog.execute("UPDATE hub_outbox SET attempts = ?, parked = 1, last_error = ? WHERE id = ?", (attempts, error, row["id"]))
                if stats is not None:
                    stats["parked"] += 1
                if self.on_rejected:
                    self.on_rejected(row, error)
                continue
            delay = min(self.max_backoff_s, BASE_BACKOFF_S * 2 ** (attempts - 1))
            next_at = (self.clock() + timedelta(seconds=delay)).isoformat()
            self.catalog.execute("UPDATE hub_outbox SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE id = ?",
                                 (attempts, next_at, error, row["id"]))
            if stats is not None and rejected:
                stats["failed"] += 1

    def retry(self, item_id: int) -> None:
        self.catalog.execute("UPDATE hub_outbox SET parked = 0, attempts = 0, next_attempt_at = NULL WHERE id = ?", (item_id,))

    def drop(self, item_id: int) -> None:
        self.catalog.execute("UPDATE hub_outbox SET sent_at = ?, last_error = 'dropped by operator' WHERE id = ? AND sent_at IS NULL",
                             (now_iso(), item_id))
