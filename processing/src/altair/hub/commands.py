"""Hub -> Altair commands (SPEC §17.4). Each runs the same code path as the
matching CLI command, is recorded in ``hub_commands`` before it runs, and is
acked with its result. A command id seen twice is acked again, not re-run."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from observatory_contracts.commands import parse_payload
from observatory_contracts.models.processing.command import Command

from altair import frames as frame_ops
from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.hub.client import HubClient, HubUnavailable
from altair.issues import resolve_issue


class CommandError(Exception):
    pass


class CommandRunner:
    def __init__(self, catalog: Catalog, config: AltairConfig, client: HubClient,
                 refresh_config: Callable[[], Any] | None = None, hub_config: Callable[[], Any] | None = None):
        self.catalog = catalog
        self.config = config
        self.client = client
        self.refresh_config = refresh_config
        self.hub_config = hub_config or (lambda: None)
        self.handlers: dict[str, Callable[[dict], dict]] = {
            "assign_frames": self._assign_frames, "night_ready": self._night_ready, "issue_waive": self._issue_waive,
            "refresh_config": self._refresh_config, "equipment_event": self._equipment_event, "set_mode": self._set_mode,
            "rerun": self._plan("rerun"), "night_include": self._plan("night_include"), "night_exclude": self._plan("night_exclude"),
            "rereference": self._plan("rereference"), "approve_fetch": self._plan("approve_fetch"), "deny_fetch": self._plan("deny_fetch"),
        }

    def poll(self) -> int:
        """Fetch pending commands, run them, ack them. Returns how many ran."""
        commands = self.client.pending_commands()
        self.catalog.set_state("last_command_poll_at", now_iso())
        for raw in commands:
            self.handle(raw)
        self.ack_unacked()
        return len(commands)

    def handle(self, raw: dict) -> dict:
        command_id = raw["id"]
        known = self.catalog.one("SELECT * FROM hub_commands WHERE id = ?", (command_id,))
        if known and known["executed_at"]:
            self._ack(command_id, known["state"], json.loads(known["result_json"] or "null"))
            return json.loads(known["result_json"] or "{}")
        if not known:
            self.catalog.execute("INSERT INTO hub_commands(id, kind, payload_json, received_at, state) VALUES (?, ?, ?, ?, 'received')",
                                 (command_id, raw["kind"], json.dumps(raw.get("payload", {})), now_iso()))
        try:
            command = Command.model_validate(raw)
            payload = parse_payload(command).model_dump(mode="json", exclude_none=True)
            handler = self.handlers.get(command.kind)
            if handler is None:
                raise CommandError(f"unsupported command kind {command.kind}")
            result, state = handler(payload), "succeeded"
        except Exception as exc:  # noqa: BLE001 - every failure is reported back to the Hub
            result, state = {"error": str(exc)}, "failed"
        self.catalog.execute("UPDATE hub_commands SET executed_at = ?, state = ?, result_json = ? WHERE id = ?",
                             (now_iso(), state, json.dumps(result), command_id))
        self._ack(command_id, state, result)
        return result

    def ack_unacked(self) -> None:
        for row in self.catalog.query("SELECT * FROM hub_commands WHERE executed_at IS NOT NULL AND acked_at IS NULL"):
            self._ack(row["id"], row["state"], json.loads(row["result_json"] or "null"))

    def _ack(self, command_id: int, state: str, result: Any) -> None:
        try:
            self.client.ack_command(command_id, state, result if isinstance(result, dict) else None)
        except HubUnavailable:
            return  # re-acked on the next poll
        self.catalog.execute("UPDATE hub_commands SET acked_at = ? WHERE id = ?", (now_iso(), command_id))

    # ── handlers ─────────────────────────────────────────────────────────
    def _rig_for(self, optical_train: str) -> str:
        for name, rig in self.config.rigs.items():
            if rig.hub and rig.hub.optical_train == optical_train:
                return name
        raise CommandError(f"no rig is mapped to optical train {optical_train}")

    def _assign_frames(self, payload: dict) -> dict:
        target_id = payload["target_id"]
        if "sha256s" in payload:
            count = frame_ops.assign(self.catalog, self.config, target_id=target_id, sha256s=payload["sha256s"], source="hub")
        else:
            sel = payload["selector"]
            count = frame_ops.assign(self.catalog, self.config, target_id=target_id, rig=self._rig_for(sel["optical_train"]),
                                     night=sel["night"], object_header=sel.get("object"), source="hub")
        return {"frames_assigned": count}

    def _night_ready(self, payload: dict) -> dict:
        rig = self._rig_for(payload["optical_train"])
        from altair import nights

        state = nights.request_close(self.catalog, self.config, rig=rig, night=payload["night"], closed_by="session_end", at=payload["at"])
        return {"rig": rig, "night": payload["night"], "state": state, "closed": state == "closed"}

    def _issue_waive(self, payload: dict) -> dict:
        with self.catalog.transaction() as tx:
            done = resolve_issue(tx, self.config, payload["fingerprint"], status="waived", resolution=f"waived:{payload['note']}")
        if not done:
            raise CommandError(f"no open issue {payload['fingerprint']}")
        return {"waived": True}

    def _refresh_config(self, payload: dict) -> dict:
        if self.refresh_config:
            self.refresh_config()
        return {"refreshed": True}

    def _equipment_event(self, payload: dict) -> dict:
        hub_config = self.hub_config()
        event = hub_config.equipment_event(payload["equipment_event_id"]) if hub_config else None
        if event is None and self.refresh_config:
            hub_config = self.refresh_config()
            event = hub_config.equipment_event(payload["equipment_event_id"]) if hub_config else None
        if event is None:
            raise CommandError(f"equipment event {payload['equipment_event_id']} isn't in the Hub config")
        rig = self._rig_for(event["optical_train"])
        with self.catalog.transaction() as tx:
            tx.execute("INSERT OR IGNORE INTO equipment_events(at, rig, kind, filter, note, hub_event_id) VALUES (?, ?, ?, ?, ?, ?)",
                       (event["at"], rig, event["kind"], event.get("filter"), event.get("note"), event["id"]))
            _plan_request(tx, "equipment_event", {"rig": rig, "event_id": event["id"]})
        return {"rig": rig, "recorded": True}

    def _set_mode(self, payload: dict) -> dict:
        with self.catalog.transaction() as tx:
            updated = tx.execute("UPDATE projects SET multi_night_mode = ? WHERE hub_target_id = ?", (payload["mode"], payload["target_id"])).rowcount
            _plan_request(tx, "set_mode", payload)
        return {"projects_updated": updated}

    def _plan(self, kind: str) -> Callable[[dict], dict]:
        def handler(payload: dict) -> dict:
            with self.catalog.transaction() as tx:
                request_id = _plan_request(tx, kind, payload)
            return {"queued": True, "plan_request_id": request_id}
        return handler


def _plan_request(tx: sqlite3.Connection, kind: str, payload: dict, source: str = "hub") -> int:
    return tx.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES (?, ?, ?, ?)",
                      (kind, json.dumps(payload), source, now_iso())).lastrowid
