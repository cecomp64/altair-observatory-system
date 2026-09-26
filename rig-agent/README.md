# rig-agent (`robs`)

The observatory-side worker for the remote observatory system: it runs
on (or near) each telescope's control PC, talks to NINA's **Target
Scheduler** plugin, and syncs with the [Hub](../hub/) (the Rails app's
JSON API).

This component was the `remote-observatory-worker` repository; it now
lives in the `altair-observatory-system` monorepo with its full history.

## Design and API contract

* [`docs/SYSTEM_ARCHITECTURE.md`](../docs/SYSTEM_ARCHITECTURE.md) is the
  system design; §8.3 describes this component and §9.2 the legacy code
  still to be removed.
* [`contracts/`](../contracts/) is the Hub API contract this agent
  relies on (`schemas/worker/`).
* The rig agent never imports code from `hub/` or `processing/`; it only
  talks to the Hub over HTTP.

## What it does

| Command | When it runs | What it does |
|---|---|---|
| `robs roof-open` | NINA sequencer "External Script" step on roof open | Fetches active targets from the Hub and upserts them into Target Scheduler: one Target Scheduler project per Hub project (`#P<id> <name>`, the project's priority, the target's minimum altitude), targets named `#<id> <name>`, and each plan's `schedule_count` as the desired count. Reports `roof_open`. |
| `robs sync-progress` | Periodically through the night (Task Scheduler / cron) | Reads accepted-frame counts out of Target Scheduler and reports them |
| `robs end-of-night` | End of the NINA sequence (the **only** end-of-sequence script needed) | A final progress sync, then `session_end` to the Hub, which tells Altair the night is over (`night_ready`), then cleanup. Uploads and stacks nothing: Altair collects, archives and processes the frames. |
| `robs session-end` | For sequences that signal the end separately | Only the `session_end` report (or Altair's marker file when standalone) |
| `robs cleanup` | Periodically, or as part of `end-of-night` | Disables targets the Hub no longer considers active, and Target Scheduler projects left with none |
| `robs check-config` | After installing or changing the config | Hub reachable and key accepted, timezone matches the Hub telescope, folders, Target Scheduler per-project support |
| `robs check-schema` | Whenever you install/upgrade Target Scheduler | Verifies the plugin's SQLite schema still matches what this worker expects |

Every command sends a heartbeat, so the Hub's admin pages show the worker's health.

### Frames

The rig agent never uploads or stacks frames: Altair collects them from `subs_dir` over
the network, archives them on the NAS and S3, and processes them. `subs_dir` is the
folder Altair has as this rig's `raw_root`, so NINA must use the file pattern from
Altair's SPEC §4.2.

### Standalone (no Hub)

With `hub: { enabled: false }` and `targets_file:` (JSON in the `active_targets` shape from
`contracts/schemas/worker/active_targets.response.json`), the worker schedules targets from
the file, writes progress to `robs_<slug>_events.jsonl` next to the Target Scheduler
database, and at end of night writes Altair's session-end marker
(`<subs_dir>/_altair/session-end-<time>.json`, or `altair_marker_dir`).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Copy `config/example.telescope.yml` to `config/<your-telescope-slug>.yml`
per telescope and fill it in — the Hub API base URL + a telescope
API key (create one at **Admin → Telescopes → API keys** in the Hub), the local path to Target Scheduler's `schedulerdb.sqlite`, where
NINA writes subs, and your NINA equipment profile GUID.
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

## How Hub targets map to Target Scheduler

We don't extend Target Scheduler's own tables with a "Hub target id"
column — that schema isn't ours to modify safely across plugin
upgrades. Instead:

* Scheduler targets are named by the Hub's `nina_name`,
  `#<hub_target_id> <name>`, and projects `#P<hub_project_id> <name>`.
  NINA writes the target name into the `OBJECT` header, which is how
  Altair links frames back to Hub targets.
* A local per-telescope SQLite side database (`robs_state.sqlite`,
  written next to `schedulerdb.sqlite`) maps Hub ids to Target
  Scheduler row ids — see `src/robs/state.py`.

## Tests

```bash
uv sync --extra dev
uv run pytest
uv run lint-imports
```

Tests don't require a real NINA install: `tests/conftest.py` builds a
throwaway SQLite database matching `scheduler_schema.py`, and HTTP calls
are mocked with `responses` and checked against `contracts/schemas/`.
