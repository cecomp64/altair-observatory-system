# processing (Altair)

Fully automated, event-triggered astrophotography pre-processing: raw frames from a night's
imaging session are calibrated and integrated headlessly with PixInsight WBPP into one master
per (telescope, camera, filter, target) per night, plus optional weighted multi-night masters
that reuse registration. Targets Windows + NINA: a processing PC collects frames from each
rig PC's NINA folder (configured per rig) onto a required NAS, which is the canonical raw
store that processing reads in place, and backs raw lights and outputs up to Amazon S3 as
the first pipeline step. Older raw lights and calibrated subs move to S3-only, with safe
cleanup and automatic re-fetch.

See [docs/SPEC.md](docs/SPEC.md) for the implementation specification.

This component was the `altair-pre-processor` repository; it now lives in the
`altair-observatory-system` monorepo with its full history. How it fits into the
whole system (Hub integration, target resolution, outbox, commands) is in
[`../docs/SYSTEM_ARCHITECTURE.md`](../docs/SYSTEM_ARCHITECTURE.md) §8.2. It never imports
code from `hub/` or `rig-agent/`; everything it shares with them is the API contract in
[`../contracts/`](../contracts/).

## What's implemented

SPEC v0.8 phases 1–8 and the Hub integration (§5.1, §17), as the Python package `altair`:

| Area | Modules |
|---|---|
| Config and catalog | `config` (`altair.yaml`, SPEC §5), `catalog` (SQLite, WAL; SPEC §6.3 + §17.5 tables) |
| Collection (§7.3) | `collector` (rig polling, stability, double read, verified NAS writes, manifests), `ingest`, `frames`, `nights`, `triggers` |
| Storage (§7) | `storage.locations` (NAS, S3 with conditional writes and checksums), `replicator`, `cleanup` (ledger, re-check before delete), `stager` (in place, cache, rig, S3, restores, approvals), `scrub`, `nas`, `s3_setup`, `catalog_backup`, `rebuild` |
| Planning (§6.4, §8) | `planner.plan`, `planner.matching` (calibration rules, rotator, equipment events), `planner.projects` |
| Processing (§6.5, §6.6) | `executor` (PixInsight runner, queue, retries, crash recovery), `pjsr/` (the PixInsight scripts), `publish` (checks, blobs, sidecars, viewing copies, Hub data products), `calibration` |
| Multi-night (§9) | `projects.merge` (gates, MERGE planning), `projects.weights` |
| Issues and alerts (§10) | `issues`, `issue_actions` (waive, resolve with a flat, flats plan), `notify` (toast, Pushover, ntfy, email), `status_page` |
| Daemon (§4.1) | `daemon` (`altair serve`; every worker on its own thread) |
| Hub (§17) | `hub.resolver`, `outbox`, `commands`, `config_sync`, `sync`, `reconcile`, `previews`; `index` (`altair index`) |

```bash
uv sync --extra dev
uv run pytest            # a fake PixInsight and a contract-checking fake Hub; whole nights end to end
uv run lint-imports      # never imports robs or hub
uv run altair --config altair.yaml doctor
uv run altair --config altair.yaml serve          # altaird in the foreground
```

- [`docs/pixinsight-cli.md`](docs/pixinsight-cli.md): the job.json/result.json contract
  with PixInsight.
- [`docs/dr-runbook.md`](docs/dr-runbook.md): disaster recovery.
- [`deploy/windows/`](deploy/windows/): the rig setup and the Task Scheduler install.

## Not verified yet

The PJSR scripts (`src/altair/pjsr/`) have never run on a real PixInsight. The SPEC
phase 0 spike on the processing PC needs to check:
- the CLI flags;
- whether `jsArguments` receives the job path;
- the process parameters and the `SubframeSelector` measurement columns.

Headless WBPP driving is one of the things to verify. Until it is,
`pixinsight.night_stack_engine` defaults to `native`: calibration, StarAlignment to
the project reference, LocalNormalization and ImageIntegration, with the same contract.

Everything else is exercised by the tests, but not yet on real rigs, a real NAS or
real nights.

`tools/e2e/altair_hub_e2e.py` runs the Hub flow against a real Hub.
