"""The job.json / result.json contract between Altair and the PJSR runner
(SPEC §6.5; docs/pixinsight-cli.md). The runner reads ``job.json`` from its
first argument, dispatches on ``kind`` (and ``phase`` for MERGE), and
**always** writes ``result.json`` in the work directory, from its catch
blocks too. A missing result.json or a non-zero exit is a failed run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = 1


@dataclass
class Output:
    role: str                     # master / calibrated_frame / reference / coverage / rejection_low / rejection_high / viewing
    path: str
    source_sha256: str | None = None   # for calibrated frames: the light it came from


@dataclass
class RunResult:
    status: str                   # ok / error
    error: str | None = None
    outputs: list[Output] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    frames: list[dict[str, Any]] = field(default_factory=list)          # per light: sha256, used, weight, fwhm, …
    measurements: list[dict[str, Any]] = field(default_factory=list)    # MERGE measure phase
    software: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def output(self, role: str) -> Output | None:
        return next((o for o in self.outputs if o.role == role), None)

    def outputs_of(self, role: str) -> list[Output]:
        return [o for o in self.outputs if o.role == role]


class ContractError(Exception):
    pass


def write_job(path: Path, job: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": SCHEMA, **job}, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return path


def read_result(work_dir: Path) -> RunResult:
    path = work_dir / "result.json"
    if not path.exists():
        raise ContractError("the runner wrote no result.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ContractError(f"result.json is not JSON: {exc}") from exc
    if raw.get("status") not in ("ok", "error"):
        raise ContractError("result.json has no status ok/error")
    outputs = []
    for o in raw.get("outputs", []):
        if "role" not in o or "path" not in o:
            raise ContractError(f"result.json output without role/path: {o}")
        outputs.append(Output(o["role"], o["path"], o.get("source_sha256")))
    return RunResult(status=raw["status"], error=raw.get("error"), outputs=outputs, metrics=raw.get("metrics") or {},
                     frames=raw.get("frames") or [], measurements=raw.get("measurements") or [], software=raw.get("software") or {},
                     raw=raw)
