# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in this repository.

## What this is

A monorepo with three independent components and a shared API contract. Read
[`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md) before changing behaviour
across components; §3.6 has the layout and the independence rules, §5 the API, §9 the
status and the outstanding work. Superseded plans are in `docs/archive/`.

| Directory | Component | Stack |
|---|---|---|
| `hub/` | The Hub: central server, UI, API, PostgreSQL | Ruby 3.3, Rails 8, Hotwire, Tailwind, RSpec |
| `rig-agent/` | Rig agent `robs` (NINA Target Scheduler sync) | Python ≥ 3.10, click, pytest, uv |
| `processing/` | Processing core Altair | Python 3.12, uv, PixInsight (planned) |
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
                                         # spec/system needs Chrome; set BROWSER_PATH if it isn't on the PATH
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
```bash
cd processing
uv sync --extra dev
uv run pytest
uv run lint-imports                      # no imports of robs or hub
```
Spec: `processing/docs/SPEC.md` (v0.8). The Hub sync (`src/altair/hub/`), catalog and
`altair index` are implemented; the PixInsight pipeline is not yet.

### End-to-end (needs a running Hub)
```bash
# with the Hub running (e.g. bin/rails server -p 3055); see each script's docstring
cd processing && uv run python ../tools/e2e/altair_hub_e2e.py --hub http://localhost:3055 \
    --hub-dir ../hub --telescope SLUG --train TRAIN_KEY
cd rig-agent && uv run python ../tools/e2e/worker_hub_e2e.py --hub http://localhost:3055 \
    --hub-dir ../hub --telescope SLUG
```

## Changing the API

1. Edit `contracts/schemas/…` and add or update an example under `contracts/examples/…`.
2. `python tools/generate_contracts.py`, then run the contract checks above.
3. Add a `contracts/CHANGELOG.md` entry; bump `API_REVISION` when a client can observe it.
4. Implement it in the Hub with a request spec using `match_api_contract`.
5. The rig agent and Altair build and parse payloads with `observatory-contracts` models.

## Paths in docs

Paths in `docs/SYSTEM_ARCHITECTURE.md` §8 are relative to their component:
`app/models/…` means `hub/app/models/…`, `src/altair/…` means `processing/src/altair/…`.
