"""Local issues (SPEC §10), deduplicated by fingerprint and mirrored to the
Hub through the outbox (SPEC §17.3)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from altair.catalog.db import now_iso
from altair.config import AltairConfig


def raise_issue(tx: sqlite3.Connection, config: AltairConfig, *, kind: str, severity: str, fingerprint: str, message: str,
                scope: dict[str, Any], requirement: dict[str, Any] | None = None) -> int:
    row = tx.execute("SELECT id, status, message, severity FROM issues WHERE fingerprint = ?", (fingerprint,)).fetchone()
    if row and row["status"] == "open" and row["message"] == message and row["severity"] == severity:
        return row["id"]
    if row:
        tx.execute("UPDATE issues SET status = 'open', severity = ?, message = ?, scope_json = ?, requirement_json = ?, "
                   "resolved_at = NULL, resolution = NULL WHERE id = ?",
                   (severity, message, json.dumps(scope), json.dumps(requirement) if requirement else None, row["id"]))
        issue_id = row["id"]
    else:
        issue_id = tx.execute(
            "INSERT INTO issues(kind, severity, status, fingerprint, scope_json, requirement_json, message, created_at) "
            "VALUES (?, ?, 'open', ?, ?, ?, ?, ?)",
            (kind, severity, fingerprint, json.dumps(scope), json.dumps(requirement) if requirement else None, message, now_iso()),
        ).lastrowid
    _mirror(tx, config, issue_id)
    return issue_id


def resolve_issue(tx: sqlite3.Connection, config: AltairConfig, fingerprint: str, *, status: str = "resolved", resolution: str = "auto") -> bool:
    row = tx.execute("SELECT id FROM issues WHERE fingerprint = ? AND status = 'open'", (fingerprint,)).fetchone()
    if not row:
        return False
    tx.execute("UPDATE issues SET status = ?, resolved_at = ?, resolution = ? WHERE id = ?", (status, now_iso(), resolution, row["id"]))
    _mirror(tx, config, row["id"])
    return True


def _mirror(tx: sqlite3.Connection, config: AltairConfig, issue_id: int) -> None:
    if config.hub.enabled:
        from altair.hub.reporters import enqueue_issue

        enqueue_issue(tx, config, issue_id)
