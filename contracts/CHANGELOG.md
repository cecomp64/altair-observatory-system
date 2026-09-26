# Hub API contract: `api_revision` history

Every change to `contracts/schemas/` that a client can observe gets an entry here, and
`API_REVISION` in `python/src/observatory_contracts/__init__.py` follows the newest
entry. Changes within `/api/v1` are additive only; a breaking change ships as `/api/v2`
alongside v1 (SYSTEM_ARCHITECTURE.md §5). Clients report the revision they
speak in heartbeats; checking it in `robs check-config` / `altair doctor` is still to do
(SYSTEM_ARCHITECTURE.md §9.4).

## api_revision 4

Zipped downloads, so the archive holds fewer, larger objects:
- Night masters' `data_product` metadata may carry `calibrated_bundle`: the night's
  calibrated subs as one zip in the archive, which is now their only S3 copy
  (`sha256`, `size_bytes`, `archive_uri`, `frames`).
- A new product kind in the URL, `masters_bundle`: a target's zip of its latest
  multi-night masters and their reports.
  - `altair_id` is the processing project, `filter` is `"all"`, and `version` is the
    build number.
  - `contents` lists the masters in the zip.
  - The zip is rewritten under one versioned key after every merge.
- The Hub offers both as presigned downloads. Older Hubs reject the unknown kind, so
  Altair parks those items, and they ignore `calibrated_bundle`.

## api_revision 3

- `PUT /processing/data_products/:kind/:altair_id` accepts an optional `report` multipart
  part: the product's Markdown report (text/markdown, UTF-8, at most 256 KB), which the
  Hub stores beside the preview and renders on the product's report page. Older Hubs
  ignore the part.

## api_revision 2

Removes the legacy worker upload path. No client ever used it in production: the rig agent
no longer uploads or stacks frames, because Altair owns all image data.

- **Removed:** `POST /targets/:id/files` (scope `files:write`) and its schemas
  `worker/target_file.request.json` / `worker/target_file.response.json`.
- **Removed:** the `file_added` value of `target_event.request.json`'s `event_type`.
- `POST /heartbeat` responses carry the Hub's `api_revision`; `robs check-config` and
  `altair doctor` compare it with the client's.
- **Widened:** a frame's `storage.s3` accepts every S3 storage class (`STANDARD`,
  `ONEZONE_IA`, `INTELLIGENT_TIERING`, `GLACIER_IR`, `GLACIER` as well as `STANDARD_IA`
  and `DEEP_ARCHIVE`), since each data class's storage class is configurable.

## api_revision 1

First published contract (Phase P0 of SYSTEM_ARCHITECTURE.md §9).

- **Worker endpoints (existing, unchanged shape):** `GET /telescopes/:slug/active_targets`,
  `PATCH /targets/:id/progress`, `POST /targets/:id/events`, `POST /targets/:id/files`
  (legacy). The Hub serves these today.
- **Worker additions (optional fields, served from P1/P5):** `active_targets` gains
  `telescope.timezone`, `nina_name`, `rotation_deg`, `min_altitude_deg`, `project`,
  `optical_train` and per-plan `schedule_count`. New `POST /telescopes/:slug/sessions`.
- **Both agents:** `POST /heartbeat`.
- **Processing endpoints (P3):** `GET /processing/config`, `POST`/`PATCH /processing/frames:batch`,
  `PUT /processing/nights/:optical_train/:night`, `GET …/digest`,
  `PUT /processing/calibration_masters/:altair_id`, `PUT /processing/data_products/:kind/:altair_id`,
  `PUT /processing/issues/:fingerprint`, `PUT /processing/jobs/:altair_id`,
  `GET /processing/commands`, `POST /processing/commands/:id/ack`, and the twelve command kinds.
