# rig-agent (`robs`)

The observatory-side worker for the remote observatory system: it runs
on (or near) each telescope's control PC, talks to NINA's **Target
Scheduler** plugin, and syncs with the [Hub](../hub/) (the Rails app's
JSON API).

This component was the `remote-observatory-worker` repository; it now
lives in the `altair-observatory-system` monorepo with its full history.

## Design and API contract

* [`docs/SYSTEM_ARCHITECTURE.md`](../docs/SYSTEM_ARCHITECTURE.md) is the
  system design. §8.3 lists the planned changes to this component
  (`data_pipeline: altair`, per-project Target Scheduler projects,
  session events, standalone `targets_file` mode).
* [`hub/ARCHITECTURE.md`](../hub/ARCHITECTURE.md) is the current
  Hub ↔ worker API contract this worker relies on. It stays in force
  until the new contract in [`contracts/`](../contracts/) replaces it.
* The rig agent never imports code from `hub/` or `processing/`; it only
  talks to the Hub over HTTP.

## What it does

| Command | When it runs | What it does |
|---|---|---|
| `robs roof-open` | NINA sequencer "External Script" step on roof open | Fetches active targets from Rails, upserts them into Target Scheduler |
| `robs sync-progress` | Periodically through the night (Task Scheduler / cron) | Reads accepted-frame counts out of Target Scheduler, reports them to Rails |
| `robs end-of-night` | Roof close / end of sequence | Uploads the night's subs to S3, optionally calibrates+stacks, reports files back, then runs cleanup |
| `robs cleanup` | Periodically, or as part of `end-of-night` | Disables/retires targets in Target Scheduler that Rails no longer considers active (i.e. completed) |
| `robs check-schema` | Whenever you install/upgrade Target Scheduler | Verifies the plugin's SQLite schema still matches what this worker expects |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Copy `config/example.telescope.yml` to `config/<your-telescope-slug>.yml`
per telescope and fill it in — the Rails API base URL + a telescope-scoped
API key (create one at **Admin → Telescopes → API keys** in the Rails
app), the local path to Target Scheduler's `schedulerdb.sqlite`, where
NINA writes subs, your S3 bucket, and your NINA equipment profile GUID.
`api_key` can instead be supplied via
`ROBS_<SLUG>_API_KEY` (and any other field via `ROBS_<SLUG>_<FIELD>`) so
it never has to live in the YAML file.

Then wire the commands into NINA's Advanced Sequencer and your OS
scheduler:

* **Roof open** (Advanced Sequencer trigger/instruction → External
  Script): `robs roof-open --config config/backyard-16in.yml`
* **Periodically overnight** (Windows Task Scheduler / cron, every
  15–30 min): `robs sync-progress --config config/backyard-16in.yml`
* **Roof close / end of sequence**: `robs end-of-night --config config/backyard-16in.yml`

Run `robs check-schema --config <file>` once after installing to catch
a Target Scheduler schema mismatch before it silently no-ops — see
`src/robs/scheduler_schema.py` for why this is worth checking (that
plugin's schema is reconstructed from its public source and isn't
something we can verify against a live install here).

## Calibration + stacking

Stacking is optional and off by default (`stacking.enabled: false`).
Two backends are supported:

* **Siril** (`stacking.backend: siril`) — generates and runs a Siril
  `.ssf` script that calibrates against `master_frames_dir` (if
  present) and stacks with sigma rejection. Requires `siril-cli` on
  `PATH` or `stacking.executable_path`.
* **PixInsight** (`stacking.backend: pixinsight`) — PixInsight has no
  single built-in "stack these" console command, so this backend shells
  out to a *site-provided* PJSR script (`stacking.pjsr_script_path`,
  typically an exported WBPP process icon).

Either way, the resulting stack and a JPEG preview (if produced) are
uploaded to S3 and reported to Rails as `kind: stacked` / `kind:
preview` files — the preview becomes the target's thumbnail in the UI.

## How targets map to local files

We don't extend Target Scheduler's own tables with a "Rails target id"
column — that schema isn't ours to modify safely across plugin
upgrades. Instead:

* Scheduler targets are named `#<rails_target_id> <name>` (see
  `sync._scheduler_target_name`), and a local per-telescope SQLite side
  database (`robs_state.sqlite`, written next to `schedulerdb.sqlite`)
  maps Rails ids to Target Scheduler row ids — see `src/robs/state.py`.
* `end_of_night.py` expects `subs_dir` to contain one subdirectory per
  target, named with that same `#<id>` prefix — which is what NINA
  produces if its sequencer's file path pattern includes the target
  name. Point NINA's image file pattern at
  `$$TARGETNAME$$/$$IMAGETYPE$$_...` and it falls out naturally.

## Tests

```bash
.venv/bin/pytest
```

Tests don't require a real NINA install, S3 bucket, or Siril/PixInsight
binary — `tests/conftest.py` builds a throwaway SQLite database matching
`scheduler_schema.py`, HTTP calls are mocked with `responses`, and S3/
subprocess calls are mocked directly.
