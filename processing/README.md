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
