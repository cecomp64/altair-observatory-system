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
| `SJAA_API_TOKEN` | Enables "Log in with SJAA". A key for the [SJAA membership API](https://github.com/sjaa/sjaa-memberships/tree/main/docs/api-reference) with `read` permission (or credentials `sjaa.api_token`) |
| `SJAA_API_URL` | SJAA membership API base URL (default `https://membership.sjaa.net/api`) |

### Log in with SJAA

SJAA's API issues keys only to SJAA admin accounts and can't check a member's
password. So the Hub uses one service key and proves a member's identity by
emailing a one-time link (30 minutes) to the address on their SJAA record; the
Hub needs working SMTP for this. Following the link finds or creates the Hub
account with that email, links it to the SJAA Person, and copies their name, email
and membership end date. A linked member's name comes from SJAA. The membership
is re-read on each SJAA login or with **Refresh** on the profile.

To get the key, an SJAA admin with `read` permission runs
`curl -u admin@example.org:PASSWORD -X POST https://membership.sjaa.net/api/keys -H 'Accept: application/json'`
and puts the returned `token` in `SJAA_API_TOKEN`.

An admin can tick **Requires an active SJAA membership** on a telescope. Members
then need a linked, current membership to choose it in the project wizard, or to
resume or reopen work on it. Admins are exempt. Targets already queued keep
running if a membership lapses.

## Notes on gem pinning

`Gemfile` pins `json` to `~> 2.9`. `json` 3.0 changed
`JSON.parse`'s arity in a way that's incompatible with how Rails 8.1's
`ActiveSupport::JSON.decode` calls it — without the pin, encrypted
session cookies fail to decrypt (`ArgumentError: wrong number of
arguments`) on every request that round-trips a session/CSRF cookie.
