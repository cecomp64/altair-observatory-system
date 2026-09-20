# Remote Observatory — Architecture

This document is the shared design reference for the two halves of the
system:

- **`remote-observatory-queueing-system`** (this repo) — a Ruby on Rails
  app. It is the source of truth for telescopes, users, targets, and
  exposure plans, and the human-facing UI (admin + end-user).
- **`remote-observatory-worker`** — a Python service/CLI that runs at (or
  near) each telescope's control computer. It talks to N.I.N.A.
  (Nighttime Imaging 'N' Astronomy) and its **Target Scheduler** plugin,
  runs at "roof open" / "roof close" / "end of night" triggers, and calls
  back into the Rails API to report progress and publish files.

Both repos keep a copy of this file; keep them in sync when the contract
changes.

## High-level flow

1. A member picks a telescope in the Rails app and creates a **Target**
   through the guided wizard: coordinates + one or more **Exposure
   Plans** (filter, count, duration).
2. An admin (or the member, if self-serve is enabled for that telescope)
   marks the target **submitted**, making it eligible to be scheduled.
3. At the observatory, NINA's sequencer fires an **External Script**
   step when the roof opens. That step invokes the worker's
   `robs roof-open --telescope <slug>` CLI.
4. The worker calls `GET /api/v1/telescopes/:slug/active_targets` on the
   Rails API (authenticated with a telescope-scoped API key) and
   upserts the results into the NINA Target Scheduler plugin's SQLite
   database, so NINA images them that night.
5. Through the night, NINA / Target Scheduler records what was actually
   captured. A periodic worker job (`robs sync-progress`) reads that and
   `PATCH`es progress back to Rails, which triggers email/Discord
   alerts.
6. At end of night (roof close / sequencer end), `robs end-of-night` runs:
   uploads the night's subs to S3, optionally kicks off calibration +
   stacking (Siril/PixInsight), uploads the stacked result and a preview
   thumbnail, and reports the file URLs + final progress to Rails via
   `POST /api/v1/targets/:id/files`.
7. A periodic `robs cleanup` job retires completed projects out of the
   Target Scheduler DB and marks them `completed` in Rails once every
   exposure plan has met its target count.

## Data model (Rails)

- **User** (Devise) — `email`, `name`, `role` (`member` / `admin`),
  `sjaa_membership_number`, `discord_webhook_url`, `notify_email`,
  `notify_discord`.
- **Telescope** — `name`, `slug`, `latitude`, `longitude`,
  `elevation_m`, `active`, `self_serve_submit` (bool), horizon file
  (Active Storage attachment, simple two-column `az,alt` text/CSV).
- **ApiKey** — belongs to a `Telescope`, `token_digest`, `name`,
  `last_used_at`, `active`. Used by the worker.
- **Target** — belongs to `User` and `Telescope`. `name`, `ra_deg`,
  `dec_deg`, `status` (`draft`/`submitted`/`active`/`in_progress`/
  `completed`/`cancelled`), `priority`, `notes`, `preview_image_url`.
- **ExposurePlan** — belongs to `Target`. `filter`, `exposure_seconds`,
  `desired_count`, `completed_count`.
- **TargetFile** — belongs to `Target`. `url`, `kind`
  (`sub`/`stacked`/`preview`/`log`), `filter`, `captured_at`.
- **TargetEvent** — belongs to `Target`. `event_type`
  (`progress`/`file_added`/`status_changed`/`error`), `payload` (jsonb),
  used to drive the activity feed and notifications.

## API contract (`/api/v1`)

Authentication: `Authorization: Bearer <api_key_token>` — the key is
scoped to one telescope; requests for another telescope's data are
rejected.

- `GET /api/v1/telescopes/:slug/active_targets`
  Returns targets with status `submitted`/`active`/`in_progress` for
  that telescope, each with its exposure plans and remaining counts.
  This is what the worker syncs into NINA Target Scheduler.

- `PATCH /api/v1/targets/:id/progress`
  Body: `{ exposure_plans: [{ id, completed_count }], status? }`.
  Updates progress; emits a `progress` `TargetEvent` which triggers
  alerts.

- `POST /api/v1/targets/:id/files`
  Body: `{ url, kind, filter?, captured_at? }`. Registers a hosted file
  (S3 URL). `kind: "preview"` also updates `Target#preview_image_url`.
  Emits a `file_added` event.

- `POST /api/v1/targets/:id/events` (generic escape hatch)
  Body: `{ event_type, payload }`.

All mutating endpoints are idempotent-friendly: the worker may resend
the same progress update safely (counts are set, not incremented).

## Notifications

`TargetEvent` creation enqueues `NotifyOwnerJob`, which — based on the
target owner's `notify_email` / `notify_discord` preferences — sends a
Rails Mailer email and/or posts to the user's personal
`discord_webhook_url` (falling back to a telescope- or system-wide
webhook if the user hasn't set one).

## SJAA membership

The San Jose Astronomical Association membership database is external
and not integrated at the data level. `User#sjaa_membership_number` is a
free-text field members can fill in on their profile, and the app links
out to the SJAA membership site (URL from `Rails.application.credentials`
/ `ENV["SJAA_MEMBERSHIP_URL"]`) so members can verify/renew. No
credentials or PII are pushed to SJAA.

## Worker configuration

Each telescope's control PC runs the worker with a small YAML config
(see `remote-observatory-worker/README.md`) naming: the Rails API base
URL + API key, the local NINA Target Scheduler SQLite path, the subs
directory NINA writes to, the S3 bucket/prefix, and optional Siril/
PixInsight settings.
