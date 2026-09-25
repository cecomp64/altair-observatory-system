# altair-pre-processor

Fully automated, event-triggered astrophotography pre-processing: raw frames from a night's
imaging session are calibrated and integrated headlessly with PixInsight WBPP into one master
per (telescope, camera, filter, target) per night, plus optional weighted multi-night masters
that reuse registration. Targets Windows + NINA: a processing PC collects frames from each
rig PC's NINA folder (configured per rig) onto a required NAS, which is the canonical raw
store that processing reads in place, and backs raw lights and outputs up to Amazon S3 as
the first pipeline step, with safe cleanup and automatic re-fetch.

See [docs/SPEC.md](docs/SPEC.md) for the implementation specification.
