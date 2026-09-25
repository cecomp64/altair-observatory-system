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

The Hub integration of SPEC v0.8 (§5.1, §17), as the Python package `altair`:

| Module | What |
|---|---|
| `altair.config` | `altair.yaml` (site, rigs with their `hub:` mapping, aliases, `hub:` block); the node key from `ALTAIR_HUB_API_KEY` or Windows Credential Manager |
| `altair.catalog` | The SQLite catalog (SPEC §6.3 tables + the §17.5 Hub columns and tables), WAL mode |
| `altair.ingest.headers` | FITS/XISF headers → canonical fields, night, rotator |
| `altair.hub.resolver` | Hub target resolution (§17.2) |
| `altair.hub.outbox` | Transactional outbox, coalescing, batching, back-off, parking (§17.3) |
| `altair.hub.commands` | Hub commands, idempotent by id (§17.4) |
| `altair.hub.config_sync`, `sync` | Config pull with ETag + cache, the `hub_sync` loop, `HUB_*` issues |
| `altair.hub.reconcile`, `previews` | Nightly digest reconciliation (§17.6); auto-STF JPEG previews |
| `altair.index` | `altair index` (§17.7) |

The collector, storage engine, planner and PixInsight executor (SPEC phases 0–8)
write to the same catalog: frames through `altair.frames.register`, night closes
through `altair.nights.close`, and they read planning requests from `plan_requests`.

```bash
uv sync --extra dev
uv run pytest            # includes a contract-checking fake Hub and offline properties
uv run lint-imports      # never imports robs or hub
uv run altair --config altair.yaml hub status
```

`tools/e2e/altair_hub_e2e.py` runs the same flow against a real Hub.
