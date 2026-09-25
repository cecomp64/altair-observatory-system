# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in this repository.

## What this is

A monorepo with three independent components and a shared API contract. Read
[`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md) before changing behaviour
across components; §3.6 has the layout and the independence rules, §5 the API, §9 the
phased plan.

| Directory | Component | Stack |
|---|---|---|
| `hub/` | The Hub: central server, UI, API, PostgreSQL | Ruby 3.3, Rails 8, Hotwire, Tailwind, RSpec |
| `rig-agent/` | Rig agent `robs` (NINA Target Scheduler sync) | Python ≥ 3.10, click, pytest, uv |
| `processing/` | Processing core Altair (spec-only so far) | Python 3.12, PixInsight (planned) |
| `contracts/` | JSON Schemas + generated pydantic models (`observatory-contracts`) | JSON Schema 2020-12, pydantic v2 |
| `tools/` | Repo-wide scripts (contract validation and codegen) | Python |

## Independence rules (enforced in CI)

- Components never import or load each other's code. They communicate only through the
  Hub's HTTP API; the only direct data path is Altair pulling image files from rig PCs.
- The only shared code is `contracts/`. The Hub reads `contracts/schemas/` **in specs
  only**, never at runtime (its Docker context is `hub/`). Python components depend on
  `contracts/python` as a path dependency.
- Each component has its own manifest and lockfile, CI workflow, release tag and deploy.
  Never add a root-level Gemfile, package.json or pyproject.
- `/api/v1` changes are additive only. New fields are optional; clients ignore unknown ones.

## Commands

Run each component's commands **from its own directory**.

### hub/
```bash
cd hub
bundle install && yarn install
bin/rails db:create db:migrate db:seed
bin/dev                                  # Rails + esbuild + Tailwind watchers
bundle exec rspec                        # includes spec/requests/api/contract_spec.rb (reads ../contracts)
bin/rubocop && bin/brakeman --no-pager && bin/bundler-audit
kamal deploy                             # Kamal builds from hub/ (repo-relative)
```

### rig-agent/
```bash
cd rig-agent
uv sync --extra dev
uv run pytest
uv run lint-imports                      # no imports of altair or hub
```

### contracts/
```bash
pip install -e "contracts/python[dev,codegen]"
python tools/validate_contracts.py       # schemas valid, examples validate, every schema has an example
python tools/generate_contracts.py       # regenerate models after a schema change (never hand-edit models/)
python tools/generate_contracts.py --check
pytest contracts/python
```

### processing/
Spec-only: `processing/docs/SPEC.md` (v0.8). When code lands it follows SPEC §15.1
(`processing/pyproject.toml`, `src/altair/`, uv lockfile, an import-linter contract
forbidding `robs` and `hub`); `processing.yml` starts running its tests automatically.

## Changing the API

1. Edit `contracts/schemas/…` and add or update an example under `contracts/examples/…`.
2. `python tools/generate_contracts.py`, then run the contract checks above.
3. Add a `contracts/CHANGELOG.md` entry; bump `API_REVISION` when a client can observe it.
4. Implement it in the Hub with a request spec using `match_api_contract`.
5. The rig agent and Altair build and parse payloads with `observatory-contracts` models.

## Paths in docs

Paths in `docs/SYSTEM_ARCHITECTURE.md` §8 are relative to their component:
`app/models/…` means `hub/app/models/…`, `src/altair/…` means `processing/src/altair/…`.
