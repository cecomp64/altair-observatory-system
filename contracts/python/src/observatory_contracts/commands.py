"""Typed payloads for Hub -> Altair commands (SYSTEM_ARCHITECTURE.md §5.4).

JSON Schema ties each command ``kind`` to its payload with if/then, which the
generated ``Command`` model can't express, so it keeps ``payload`` as a dict.
``parse_payload`` gives the typed model for a command's kind.
"""
from __future__ import annotations

from pydantic import BaseModel, RootModel

from .models.processing import command as _c

PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "assign_frames": _c.AssignFramesPayload,
    "rerun": _c.RerunPayload,
    "night_include": _c.NightSelectionPayload,
    "night_exclude": _c.NightSelectionPayload,
    "issue_waive": _c.IssueWaivePayload,
    "rereference": _c.RereferencePayload,
    "set_mode": _c.SetModePayload,
    "approve_fetch": _c.FetchDecisionPayload,
    "deny_fetch": _c.FetchDecisionPayload,
    "equipment_event": _c.EquipmentEventPayload,
    "refresh_config": _c.RefreshConfigPayload,
    "night_ready": _c.NightReadyPayload,
}


def parse_payload(command: _c.Command) -> BaseModel:
    """Validate ``command.payload`` against the model for ``command.kind``.

    Root models (``assign_frames`` is one of two shapes) are unwrapped, so the
    result is always the concrete payload model.
    """
    payload = PAYLOAD_MODELS[command.kind].model_validate(command.payload)
    return payload.root if isinstance(payload, RootModel) else payload
