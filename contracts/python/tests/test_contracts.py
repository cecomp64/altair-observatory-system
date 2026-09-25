"""Every example in contracts/examples parses with its generated model, and the
model's output still validates against the JSON Schema, so the schemas, the
examples and the Python models can't drift apart."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing import Registry, Resource

from observatory_contracts import API_REVISION
from observatory_contracts.commands import PAYLOAD_MODELS, parse_payload
from observatory_contracts.models.processing.command import NightReadyPayload
from observatory_contracts.models.processing.commands_response import CommandsResponse
from observatory_contracts.models.processing.frames_batch_request import FramesBatchRequest
from observatory_contracts.models.worker.active_targets_response import ActiveTargetsResponse

CONTRACTS = Path(__file__).resolve().parents[2]
SCHEMAS = CONTRACTS / "schemas"
EXAMPLES = CONTRACTS / "examples"
EXAMPLE_FILES = sorted(EXAMPLES.rglob("*.json"))


def _schema(rel: str) -> dict:
    return json.loads((SCHEMAS / rel).read_text())


REGISTRY = Registry().with_resources(
    (s["$id"], Resource.from_contents(s))
    for s in (json.loads(p.read_text()) for p in SCHEMAS.rglob("*.json"))
)


def _model_for(schema_rel: str):
    """schemas/worker/active_targets.response.json -> models.worker.active_targets_response.ActiveTargetsResponse"""
    module = "observatory_contracts.models." + schema_rel.removesuffix(".json").replace("/", ".").replace(".response", "_response").replace(".request", "_request").replace(".metadata", "_metadata")
    return getattr(importlib.import_module(module), _schema(schema_rel)["title"])


@pytest.mark.parametrize("example", EXAMPLE_FILES, ids=lambda p: p.relative_to(EXAMPLES).as_posix())
def test_example_round_trips_through_model(example: Path) -> None:
    schema_rel = example.parent.relative_to(EXAMPLES).as_posix() + ".json"
    data = json.loads(example.read_text())

    model = _model_for(schema_rel).model_validate(data)
    dumped = model.model_dump(mode="json", exclude_unset=True)

    validator = Draft202012Validator(_schema(schema_rel), registry=REGISTRY, format_checker=Draft202012Validator.FORMAT_CHECKER)
    assert list(validator.iter_errors(dumped)) == []


def test_current_hub_response_parses_without_revision_1_fields() -> None:
    data = json.loads((EXAMPLES / "worker/active_targets.response/current.json").read_text())
    target = ActiveTargetsResponse.model_validate(data).targets[0]
    assert target.nina_name is None
    assert target.exposure_plans[0].schedule_count is None


def test_unknown_fields_are_tolerated() -> None:
    data = json.loads((EXAMPLES / "worker/active_targets.response/revision1.json").read_text())
    data["targets"][0]["some_future_field"] = {"x": 1}
    ActiveTargetsResponse.model_validate(data)


def test_bad_sha256_is_rejected() -> None:
    data = json.loads((EXAMPLES / "processing/frames_batch.request/basic.json").read_text())
    data["frames"][0]["sha256"] = "ABC"
    with pytest.raises(ValidationError):
        FramesBatchRequest.model_validate(data)


def test_batches_are_capped_at_500_frames() -> None:
    frame = json.loads((EXAMPLES / "processing/frames_batch.request/basic.json").read_text())["frames"][0]
    with pytest.raises(ValidationError):
        FramesBatchRequest.model_validate({"frames": [frame] * 501})


def test_every_command_kind_has_a_payload_model() -> None:
    kinds = _schema("processing/command.json")["properties"]["kind"]["enum"]
    assert sorted(PAYLOAD_MODELS) == sorted(kinds)


def test_example_commands_parse_to_typed_payloads() -> None:
    data = json.loads((EXAMPLES / "processing/commands.response/pending.json").read_text())
    commands = CommandsResponse.model_validate(data).root
    assert {c.kind for c in commands} >= {"assign_frames", "night_ready"}
    for command in commands:
        parse_payload(command)
    night_ready = next(c for c in commands if c.kind == "night_ready")
    assert isinstance(parse_payload(night_ready), NightReadyPayload)


def test_night_ready_payload_requires_session_end() -> None:
    with pytest.raises(ValidationError):
        NightReadyPayload.model_validate({"optical_train": "t", "night": "2026-09-24", "at": "2026-09-25T12:00:00Z", "closed_by": "quiescence"})


def test_api_revision_matches_changelog() -> None:
    changelog = (CONTRACTS / "CHANGELOG.md").read_text()
    assert f"## api_revision {API_REVISION}" in changelog
