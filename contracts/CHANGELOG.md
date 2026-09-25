# Hub API contract: `api_revision` history

Every change to `contracts/schemas/` that a client can observe gets an entry here, and
`API_REVISION` in `python/src/observatory_contracts/__init__.py` follows the newest
entry. Changes within `/api/v1` are additive only; a breaking change ships as `/api/v2`
alongside v1 (SYSTEM_ARCHITECTURE.md §5). Clients report the revision they
speak in heartbeats; checking it in `robs check-config` / `altair doctor` is still to do
(SYSTEM_ARCHITECTURE.md §9.4).

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
