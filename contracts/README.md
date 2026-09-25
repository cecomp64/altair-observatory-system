# contracts/

The Hub API contract: the only thing `hub/`, `rig-agent/` and `processing/` share
(SYSTEM_ARCHITECTURE.md §3.6.2). Components never import each other's code; they agree
on these schemas instead.

| Path | What |
|---|---|
| `schemas/` | JSON Schema (2020-12) for every request and response in SYSTEM_ARCHITECTURE.md §5. `worker/` = rig agent endpoints, `processing/` = Altair endpoints, `shared/` = definitions, errors and heartbeat. |
| `examples/` | Example payloads. `examples/<schema path without .json>/<name>.json` is validated against `schemas/<schema path>.json`. |
| `python/` | Package `observatory-contracts`: pydantic v2 models **generated** from `schemas/` (`observatory_contracts.models`), plus `API_REVISION` and typed command payloads (`observatory_contracts.commands`). |
| `CHANGELOG.md` | `api_revision` history. |

## Rules

- **Additive only within `/api/v1`.** New fields are optional; clients ignore fields they
  don't know (the generated models do). A breaking change is a new `/api/v2`.
- **Every schema change** gets a `CHANGELOG.md` entry, and bumps `API_REVISION` when a
  client can observe it.
- **Never edit `python/src/observatory_contracts/models/` by hand.** Change the schema and
  regenerate.
- **Every endpoint schema has at least one example.** CI fails otherwise.

## Working on the contract

```bash
pip install -e "contracts/python[dev,codegen]"
python tools/validate_contracts.py        # schemas are valid, examples validate, coverage
python tools/generate_contracts.py        # regenerate the pydantic models
python tools/generate_contracts.py --check
pytest contracts/python                   # models round-trip every example
```

A change under `contracts/` runs every component's CI (`.github/workflows/`), so a schema
change that breaks the Hub, the rig agent or Altair fails before it merges.

## Who uses it

- **Hub** (`hub/`): request specs validate real API responses against `schemas/`
  (`hub/spec/requests/api/contract_spec.rb`). Test-only; the Hub never reads
  `contracts/` at runtime, so its Docker build context stays `hub/`.
- **Rig agent** and **processing core**: depend on `observatory-contracts` as a path
  dependency (`../contracts/python`) once they start building payloads with it
  (SYSTEM_ARCHITECTURE.md §8.2.6, §8.3).
