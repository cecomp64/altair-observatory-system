# altair-pre-processor

Fully automated, event-triggered astrophotography pre-processing: raw frames from a night's
imaging session are calibrated and integrated headlessly with PixInsight WBPP into one master
per (telescope, camera, filter, target) per night, plus optional weighted multi-night masters
that reuse registration. Targets Windows + NINA with per-rig capture PCs, a shared landing
folder, and S3 (or NFS) archive with automatic re-fetch of any needed data.

See [docs/SPEC.md](docs/SPEC.md) for the implementation specification.
