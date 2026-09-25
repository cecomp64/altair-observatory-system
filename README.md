# altair-pre-processor

Fully automated, event-triggered astrophotography pre-processing: raw frames from a night's
imaging session are calibrated and integrated headlessly with PixInsight WBPP into one master
per (telescope, camera, filter, target) per night, plus optional weighted multi-night masters
that reuse registration. Targets Windows + NINA with per-rig capture PCs, a shared landing
folder, and Altair-managed backup of raw lights and outputs to Amazon S3 (and an optional
NFS archive) as the first pipeline step, with safe cleanup and automatic re-fetch.

See [docs/SPEC.md](docs/SPEC.md) for the implementation specification.
