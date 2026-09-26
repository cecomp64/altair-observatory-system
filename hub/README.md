# Hub — Remote Observatory Queueing System

The Rails frontend and API for a remote/robotic telescope observing
queue: members pick a telescope, submit imaging targets through a guided
wizard, and track progress; admins manage telescopes and API keys; a
companion worker (the [rig agent](../rig-agent/))
uses the JSON API to sync targets into NINA's Target Scheduler plugin and
report progress and session events back.

See [`docs/SYSTEM_ARCHITECTURE.md`](../docs/SYSTEM_ARCHITECTURE.md) for the design (this
app as the central Hub for the [rig agent](../rig-agent/) and the
[processing core](../processing/)), and [`contracts/`](../contracts/) for the API
contract. The app's earlier design doc is archived in `docs/archive/`.

This component was the `remote-observatory-queueing-system` repository; it now
lives in `hub/` of the `altair-observatory-system` monorepo with its full history.
Run every command below from `hub/`.

## Requirements

* Ruby 3.3+
* PostgreSQL 14+
* Node.js (for `jsbundling-rails` / esbuild) and either `bun` or `yarn`

## Setup

```bash
bundle install
bin/rails db:create db:migrate db:seed
bin/dev            # runs Rails + esbuild + Tailwind watchers together
```

`db:seed` creates two dev accounts (both password `password123`):

* `admin@example.com` — admin
* `member@example.com` — member, with a sample in-progress target

It also creates a sample telescope with a synthetic horizon file and
prints a dev API key for it — use that key to try the API described
below (or generate a fresh one at **Admin → Telescopes → API keys**).

## Running tests

```bash
bin/rails db:test:prepare
bundle exec rspec
```

## Key areas of the app

* `app/controllers/project_wizard_controller.rb` — the multi-step,
  session-backed "new project" flow (`/projects/new`: objects → telescope →
  exposures → review; `/targets/new` redirects here).
* `app/controllers/admin/` — admin-only telescope + API key management
  (`/admin`).
* `app/controllers/api/v1/` — the API the rig agent and Altair call:
  API-key authenticated, with scopes. The contract is `../contracts/schemas/`
  (docs/SYSTEM_ARCHITECTURE.md §5).
* `app/models/telescope.rb#horizon_points` — parses a telescope's
  uploaded horizon file (az,alt CSV) for the horizon chart on the
  telescope page.
* `app/jobs/notify_owner_job.rb` / `app/services/discord_notifier.rb` —
  email + Discord alerts fired whenever a `TargetEvent` is created
  (progress update, file published, status change).

## Configuration (ENV)

| Variable | Purpose |
|---|---|
| `APP_HOST` | Host used to build absolute URLs in production emails |
| `DISCORD_DEFAULT_WEBHOOK_URL` | Fallback Discord webhook for users who opt in but haven't set their own |
| `SJAA_MEMBERSHIP_URL` | Link shown on the profile page to the SJAA membership site |

## Notes on gem pinning

`Gemfile` pins `json` to `~> 2.9`. `json` 3.0 changed
`JSON.parse`'s arity in a way that's incompatible with how Rails 8.1's
`ActiveSupport::JSON.decode` calls it — without the pin, encrypted
session cookies fail to decrypt (`ArgumentError: wrong number of
arguments`) on every request that round-trips a session/CSRF cookie.
