# Altair Pre-Processor — Implementation Specification

**Status:** Draft v0.1
**Date:** 2026-09-25

---

## 1. Purpose

Altair Pre-Processor is a fully automated, event-triggered pipeline that takes the raw
light frames from a night's imaging session and turns them into calibrated, registered,
integrated master lights — with no human in the loop.

For every unique **(telescope, camera, filter, target)** tuple found in a session, the
pipeline produces exactly **one master light**, calibrated with the correct darks, flats,
and bias / dark-flat frames for that equipment configuration.

Calibration and integration are done by **PixInsight's WeightedBatchPreprocessing (WBPP)**
script, driven headlessly. The pipeline's own job is everything around WBPP: detecting that
a session is finished, reading and normalizing metadata, grouping frames, choosing the
right calibration masters, building and running WBPP jobs, checking the results, and
filing the outputs.

### 1.1 Goals

- **Zero-touch:** a session that ends at dawn has masters ready, with no manual steps.
- **Correct calibration matching:** every light is calibrated against frames that match its
  optical train, sensor settings, and time validity window, or it is flagged. It is never
  quietly miscalibrated.
- **Deterministic and idempotent:** reprocessing the same inputs gives the same outputs, and
  re-triggering a session that is already processed does nothing unless forced.
- **Multi-rig:** several telescopes and cameras, possibly imaging at the same time, are
  supported.
- **Auditable:** every master traces back to the exact lights, calibration masters, WBPP
  settings, and software versions used to make it.

### 1.2 Non-goals (v1)

- Post-processing (gradient removal, color calibration, stretching, deconvolution).
- Acquisition control (the pipeline consumes files; it does not drive the mount or camera).
- A GUI. Configuration is YAML, and status comes through logs, notifications, and a small
  read-only status endpoint or CLI.
- Cloud or distributed processing. v1 runs on one processing host.

---

## 2. Terminology

| Term | Meaning |
|---|---|
| **Light** | A science frame of a target. |
| **Dark** | A frame taken with the shutter closed or the scope covered, at the light's exposure, gain, offset, and temperature. |
| **Flat** | A frame of an evenly lit field. It captures vignetting and dust for one optical train and filter. |
| **Bias** | A zero-length (or minimum-length) exposure. It captures read-out offset. |
| **Dark-flat (flat-dark)** | A dark taken at the flat's exposure. It replaces bias for flat calibration on CMOS sensors. |
| **Master** | The integrated result of a set of frames of one type (master dark, master flat, master light). |
| **Rig** | A (telescope, camera) pair, plus optional accessories that change the optical train (reducer, rotator angle). |
| **Session** | All frames captured on one observing night, defined as the local noon-to-noon window at the observatory. |
| **Tuple / stack key** | `(telescope, camera, filter, target)`. This identifies one output master light. |
| **Calibration library** | The long-lived, indexed store of calibration masters, reused across sessions. |

---

## 3. High-Level Architecture

```
            ┌───────────────────────────────────────────────────────────────┐
            │                        Altair service                          │
            │                                                                │
 capture ──►│  1. Trigger     2. Ingest &      3. Planner      4. Executor   │
 PC/NAS     │  Detector ────► Catalog ───────► (group +   ───► (PixInsight │
 (inbox)    │  (fs watch,     (FITS/XISF       calib match)    WBPP runner)│
            │   sentinel,     headers →              │               │       │
            │   schedule)     SQLite)                │               ▼       │
            │                                        │         5. Verifier & │
            │                  Calibration  ◄────────┘            Publisher  │
            │                  Library  ◄─── master calib build ──────┘      │
            │                                                     │          │
            │                                              6. Notifier       │
            └─────────────────────────────────────────────────────┼──────────┘
                                                                  ▼
                                           archive/ masters/ reports/ + push/email
```

The **orchestrator** is a long-running Python service (`altaird`). PixInsight is an
external worker that the orchestrator starts once per job, as a separate process.

### 3.1 Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Orchestrator language | Python 3.12+ | Mature FITS tooling (`astropy`), fs-watching (`watchdog`), and easy subprocess control. |
| Header I/O | `astropy.io.fits`; a small XISF header reader (XML header block) | Both FITS and XISF inputs are supported. |
| State store | SQLite (WAL mode) through SQLAlchemy or plain `sqlite3` | Single host, transactional, and needs no server. |
| Config | YAML validated with `pydantic` | Typed and documented, with early failure on bad config. |
| Job execution | PixInsight CLI in automation mode, running a PJSR (PixInsight JavaScript) wrapper around WBPP | Required by the project goal. |
| Headless display (Linux) | `Xvfb` / `xvfb-run` | PixInsight is a Qt GUI app and needs an X display even in automation mode. |
| Packaging | `pyproject.toml`, and a `systemd` unit (Linux) or Windows service (NSSM) | Simple to deploy on the imaging or processing host. |

---

## 4. Directory Layout (runtime data)

All roots can be configured. The defaults are:

```
/astro/
├── inbox/                     # capture software writes here (or a synced NAS share)
│   └── 2026-09-24/            # optional; the pipeline does not rely on folder names
├── work/                      # scratch space per job (WBPP output dir), deleted on success
│   └── <job-id>/
├── calibration/               # the Calibration Library
│   ├── raw/                   # archived raw calibration subs
│   └── masters/
│       ├── darks/<camera>/<gain>_<offset>_<temp>C_<exp>s_bin<b>_<date>.xisf
│       ├── bias/<camera>/...
│       ├── darkflats/<camera>/...
│       └── flats/<telescope>/<camera>/<filter>/<date>_<rot>.xisf
├── archive/                   # raw lights moved here after successful processing
│   └── <target>/<telescope>_<camera>/<filter>/<session-date>/
├── masters/                   # published outputs
│   └── <target>/<session-date>/
│       ├── <target>_<telescope>_<camera>_<filter>_<session-date>_<N>x<exp>s.xisf
│       └── <same>.json        # provenance sidecar
├── rejected/                  # frames excluded (bad metadata, no calibration, QA fail)
└── state/
    ├── altair.db              # SQLite catalog
    └── logs/
```

---

## 5. Configuration

A single `altair.yaml`, validated when the service starts. Example:

```yaml
site:
  name: "Backyard Observatory"
  timezone: "America/Los_Angeles"
  session_rollover_local: "12:00"     # noon-to-noon session boundary

paths:
  inbox: /astro/inbox
  work: /astro/work
  calibration: /astro/calibration
  archive: /astro/archive
  masters: /astro/masters
  rejected: /astro/rejected
  state: /astro/state

pixinsight:
  executable: /opt/PixInsight/bin/PixInsight.sh
  wbpp_script: /opt/PixInsight/src/scripts/BatchProcessing/WeightedBatchPreprocessing/WeightedBatchPreprocessing.js
  xvfb: true                         # wrap with xvfb-run on Linux
  instance_slot: 5                   # dedicated -n slot, so it never collides with an interactive session
  timeout_minutes: 360
  max_concurrent_jobs: 1             # WBPP is memory/IO heavy; 1 is the safe default

triggers:
  sentinel_filename: "SESSION_COMPLETE"   # written by the capture software's end-of-sequence script
  quiescence_minutes: 45                  # no new files for this long → session considered done
  scheduled_fallback_local: "09:00"       # process anything pending at this time every day
  min_lights_per_stack: 5

# Maps raw FITS keywords to canonical fields. Keyed by rig, because capture software differs.
header_mapping:
  default:
    image_type: [IMAGETYP, FRAMETYP]
    telescope:  [TELESCOP]
    camera:     [INSTRUME]
    filter:     [FILTER]
    target:     [OBJECT]
    exposure:   [EXPTIME, EXPOSURE]
    gain:       [GAIN]
    offset:     [OFFSET]
    sensor_temp:[CCD-TEMP, SET-TEMP]
    binning_x:  [XBINNING]
    binning_y:  [YBINNING]
    date_obs:   [DATE-OBS]
    rotator:    [ROTATANG, ROTATOR]
    readout_mode: [READOUTM]
    bayer_pattern: [BAYERPAT]
  # optional per-camera overrides

# Normalization: raw header values → canonical names. Regex keys are allowed.
aliases:
  telescope:
    "Esprit 100ED|ESPRIT100": esprit100
    "RASA 8": rasa8
  camera:
    "ZWO ASI2600MM Pro": asi2600mm
    "ZWO ASI533MC Pro": asi533mc
  filter:
    "^(Ha|H-alpha|HA|Halpha)$": Ha
    "^(OIII|O3)$": OIII
    "^(SII|S2)$": SII
    "^(L|Lum|Luminance)$": L
  target:
    "^(M 31|M31|Andromeda)$": M31

cameras:
  asi2600mm: { type: mono, pixel_size_um: 3.76 }
  asi533mc:  { type: osc,  pixel_size_um: 3.76, bayer_pattern: RGGB }

calibration_matching:
  dark:
    exposure_tolerance_s: 0.0        # exact match; see 7.3 for dark scaling
    temp_tolerance_c: 2.0
    max_age_days: 180
    allow_exposure_scaling: false
  flat:
    max_age_days: 30                 # flats are invalid once the optical train changes
    rotator_tolerance_deg: 1.0
    require_same_session_if_available: true
  bias:
    max_age_days: 365
  darkflat:
    exposure_tolerance_s: 0.1
    max_age_days: 180
  flat_dark_strategy: darkflat_preferred   # darkflat_preferred | bias_only | darkflat_only

wbpp:
  profile: default                   # the named profile below
  profiles:
    default:
      calibration: { optimize_darks: false, pedestal_dn: 0 }
      cosmetic_correction: { enabled: true, mode: auto_detect, hot_sigma: 3.0, cold_sigma: 3.0 }
      debayer: { method: VNG }       # OSC only
      subframe_weighting: PSF_SIGNAL_WEIGHT
      minimum_weight: 0.05           # drop frames below this normalized weight
      registration: { reference: auto, distortion_correction: false }
      local_normalization: true
      integration:
        rejection: auto              # WBPP picks by frame count
        large_scale_rejection: false
      drizzle: { enabled: false, scale: 1, drop_shrink: 0.9 }
      autocrop: true
      save_calibrated_frames: false
  overrides:                         # optional per-(telescope, camera) or per-target overrides
    - match: { camera: asi533mc }
      set: { drizzle: { enabled: true, scale: 1 } }

outputs:
  archive_raw_lights: true
  delete_work_dir_on_success: true
  include_per_night_masters: true
  cumulative_masters: false          # see §11 (future)

notifications:
  on: [success, partial, failure]
  channels:
    - type: pushover
      token_env: PUSHOVER_TOKEN
      user_env: PUSHOVER_USER
    - type: email
      smtp_url_env: SMTP_URL
      to: me@example.com
```

---

## 6. Components

### 6.1 Trigger Detector

This component decides **when** a session is ready to process. Several triggers can
fire, and they all feed one debounced "session ready" event.

1. **Sentinel file (preferred).** The capture software (NINA "External Script" at the end of
   the sequence, SGP end-of-sequence event, or an Ekos script) writes
   `<inbox>/SESSION_COMPLETE`, optionally with a JSON body listing the rigs it covers. The
   service reacts right away.
2. **Quiescence.** A `watchdog` observer tracks file create and modify events in the
   inbox. When no new light has appeared for `quiescence_minutes` *and* the local time is
   after astronomical dawn (computed with `astropy` from the site location), the session is
   considered done. This catches sequences that abort without writing a sentinel.
3. **Scheduled fallback.** A daily timer (`scheduled_fallback_local`) processes any pending
   session. This covers missed file-system events (SMB and NFS shares often drop inotify
   events).
4. **Manual.** `altair run --session 2026-09-24 [--force]` from the CLI.

**File stability:** a file is only ingested once its size and mtime have not changed for
30 seconds, *and* it opens and parses as a valid FITS or XISF file. This avoids reading
partial writes, especially over network shares.

**Debounce and lock:** a trigger for a session that is already queued or running is merged
into the existing job. Each session has a row in `sessions` with a status
(`collecting → ready → planning → running → done | partial | failed`).

### 6.2 Ingest & Catalog

For each stable file:

1. Read the headers (FITS primary HDU; XISF `<FITSKeyword>` elements and `<Property>`
   elements).
2. Resolve canonical fields through `header_mapping` (first keyword present wins), then
   normalize them through `aliases`.
3. Classify `image_type` as `LIGHT | DARK | FLAT | BIAS | DARKFLAT`. Recognize the common
   variants: `Light Frame`, `LIGHT`, `Dark Frame`, `FLATDARK`, `DARKFLAT`, `Flat Field`,
   `Bias Frame`, `OFFSET`. If a frame is typed as DARK but its exposure matches a flat set
   in the same session, reclassify it as DARKFLAT.
4. Compute `session_date` from `DATE-OBS` (UTC), converted to site time, with the noon
   rollover applied.
5. Compute a content hash (xxhash64 of the file bytes) for idempotency.
6. Insert into `frames`. Frames that are missing a required field go to
   `frames.status = 'invalid'` with a reason, and are moved to `rejected/` only after the
   session finishes processing, so the user has time to fix headers.

**Required fields by type:**

| Type | Required |
|---|---|
| LIGHT | telescope, camera, filter\*, target, exposure, gain\*\*, binning, date_obs |
| FLAT | telescope, camera, filter\*, exposure, binning, date_obs |
| DARK / DARKFLAT | camera, exposure, gain\*\*, binning, sensor_temp\*\*\* |
| BIAS | camera, gain\*\*, binning |

\* For OSC cameras with no filter wheel, filter defaults to `OSC` (or the configured
`default_filter` for the rig, for example a fixed dual-band filter).
\*\* Required only for cameras that report gain (CMOS). CCDs may leave it blank.
\*\*\* Required only when the camera is cooled (set in camera config).

### 6.3 Data Model (SQLite)

```sql
CREATE TABLE frames (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  content_hash TEXT NOT NULL,
  image_type TEXT NOT NULL,             -- LIGHT/DARK/FLAT/BIAS/DARKFLAT
  session_date TEXT NOT NULL,           -- YYYY-MM-DD (site local, noon rollover)
  date_obs TEXT NOT NULL,               -- ISO UTC
  telescope TEXT, camera TEXT, filter TEXT, target TEXT,
  exposure REAL, gain INTEGER, offset INTEGER, sensor_temp REAL,
  binning TEXT,                         -- "1x1"
  readout_mode TEXT, rotator REAL,
  width INTEGER, height INTEGER, bayer_pattern TEXT,
  raw_headers_json TEXT,
  status TEXT NOT NULL,                 -- new/valid/invalid/processed/archived/rejected
  status_reason TEXT,
  job_id INTEGER REFERENCES jobs(id)
);
CREATE INDEX frames_stack_key ON frames(session_date, telescope, camera, filter, target, image_type);

CREATE TABLE calibration_masters (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                   -- DARK/FLAT/BIAS/DARKFLAT
  path TEXT UNIQUE NOT NULL,
  camera TEXT NOT NULL, telescope TEXT, filter TEXT,
  exposure REAL, gain INTEGER, offset INTEGER, sensor_temp REAL,
  binning TEXT, readout_mode TEXT, rotator REAL,
  width INTEGER, height INTEGER,
  created_from_session TEXT,            -- date of the source calibration subs
  n_frames INTEGER,
  valid_from TEXT, valid_until TEXT,    -- explicit validity window (nullable)
  superseded_by INTEGER REFERENCES calibration_masters(id),
  quality_json TEXT                     -- noise, median, hot-pixel count, etc.
);

CREATE TABLE sessions (
  session_date TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  first_frame_at TEXT, last_frame_at TEXT,
  triggered_by TEXT, updated_at TEXT
);

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  session_date TEXT NOT NULL,
  kind TEXT NOT NULL,                   -- CALIB_MASTER / LIGHT_STACK
  stack_key_json TEXT NOT NULL,         -- {"telescope":..,"camera":..,"filter":..,"target":..}
  plan_json TEXT NOT NULL,              -- the exact inputs + settings (see §6.4)
  plan_hash TEXT NOT NULL,              -- idempotency key
  status TEXT NOT NULL,                 -- queued/running/succeeded/failed/skipped
  attempts INTEGER DEFAULT 0,
  started_at TEXT, finished_at TEXT,
  output_path TEXT, log_path TEXT, error TEXT,
  UNIQUE(plan_hash)
);
```

### 6.4 Planner

The Planner runs once a session is `ready`. It turns catalog rows into a list of jobs.

**Step 1: Build calibration masters from this session's calibration subs.**

- Group this session's raw DARK frames by `(camera, exposure, gain, offset, binning,
  readout_mode, round(sensor_temp))`. Do the same for BIAS and DARKFLAT (DARKFLAT is
  grouped by its own exposure).
- Group FLAT frames by `(telescope, camera, filter, binning, rotator_bucket)`.
- Each group with at least `min_calib_frames` (default 15) becomes a `CALIB_MASTER` job.
  New master flats need their own dark-flat or bias. Those are resolved the same way as
  light calibration in Step 3 below, so jobs are topologically ordered:
  bias/darkflat → dark → flat → lights.

**Step 2: Group lights into stack keys.**

- `stack_key = (telescope, camera, filter, target)`, restricted to this session.
- Split a stack further if any of these differ within it: `exposure`, `gain`, `offset`,
  `binning`, `readout_mode`, or `rotator` beyond tolerance. Each split needs different
  darks or flats. WBPP handles several exposures within one integration (it calibrates
  each against its matching dark and integrates them together). By default, one master is
  produced per tuple as required, and mixed exposures are integrated together. Mixed
  binning or rotator angles (beyond tolerance) go to separate sub-stacks, which is logged
  as a warning.
- Drop stacks with fewer than `min_lights_per_stack` lights (the frames are held, not
  rejected, so a later night can use them if cumulative mode is enabled).

**Step 3: Match calibration (see §7).** For each light group, pick a master dark, master
flat, and (if needed for the flat) a master bias or dark-flat. The result is a
**calibration plan**. If any required master is missing, the stack is set to
`skipped: missing_calibration`, the notification lists what is needed (for example
"Dark: asi2600mm, 300s, G100, O50, -10°C, 1x1"), and the lights stay in the inbox so they
are picked up automatically once calibration arrives.

**Step 4: Emit jobs.** Each job's `plan_json` holds the sorted list of input frame paths
and hashes, the calibration master paths and hashes, the fully resolved WBPP settings
(profile plus overrides), and the versions of Altair, PixInsight, and WBPP. `plan_hash`
is the SHA-256 of that canonical JSON. If a job with the same `plan_hash` has already
succeeded, it is skipped (idempotency). `--force` bypasses this check.

### 6.5 Executor (PixInsight / WBPP runner)

#### 6.5.1 Invocation

Each job runs as one PixInsight process:

```bash
xvfb-run -a -s "-screen 0 1920x1080x24" \
  /opt/PixInsight/bin/PixInsight.sh \
    -n=5 \
    --automation-mode \
    --no-startup-scripts \
    --force-exit \
    -r="/opt/altair/pjsr/altair_wbpp_runner.js,/astro/work/<job-id>/job.json"
```

- `--automation-mode` suppresses interactive dialogs and message boxes.
- `-n=<slot>` runs a dedicated instance slot, so it never collides with an interactive
  session the user may have open.
- `-r=<script>,<args>` runs a PJSR script. The arguments reach the script through the
  global `jsArguments` array.
- `--force-exit` makes PixInsight quit when the script finishes.

> **Verification item:** CLI flag spelling and behaviour vary between PixInsight releases
> (1.8.9-x vs 1.9.x). Phase 0 (§12) must confirm these against `PixInsight.sh --help` on the
> installed build, and pin the working set in `pixinsight.extra_args`.

The orchestrator:

1. Creates `work/<job-id>/` and writes `job.json` (all inputs, calibration masters, WBPP
   settings, output directory, and a `result.json` path).
2. Starts PixInsight with a hard timeout (`timeout_minutes`). stdout and stderr are
   streamed to `state/logs/jobs/<job-id>.log`.
3. Waits for exit, then reads `work/<job-id>/result.json`, which the PJSR script always
   writes, including from its catch blocks. A missing `result.json` or a non-zero exit
   means the job failed.
4. On timeout, kills the whole process group (PixInsight starts helper processes) and
   removes the slot's lock files so the next run starts cleanly.

#### 6.5.2 The PJSR wrapper (`pjsr/altair_wbpp_runner.js`)

WBPP is written as an interactive script. It is driven headlessly by loading its engine
and filling it in programmatically, without opening its dialog. The wrapper:

1. Reads `job.json` (the path comes from `jsArguments[0]`).
2. `#include`s WBPP's engine sources from the installed WBPP directory. Main-dialog
   execution is skipped by pre-defining the guard WBPP uses for its `main()` entry point,
   or, if the installed version does not have one, by including only the engine and
   helper files rather than the top-level script.
3. Creates the WBPP engine object, resets it to defaults, and applies settings from
   `job.json`: output directory, cosmetic correction, debayer, weighting, registration,
   local normalization, integration, drizzle, and autocrop.
4. Adds files: lights, plus the **pre-built** masters for dark, flat, and bias. The masters
   are added as master frames (WBPP recognizes files flagged as masters, or with
   `IMAGETYP = Master …`) so WBPP does not rebuild them.
5. Sets **grouping keywords** as a safety net: `TELESCOP`, `INSTRUME`, `FILTER`, and
   `OBJECT`. Even if a job accidentally held mixed data, WBPP would not cross-contaminate
   groups.
6. Runs WBPP's diagnostics or validation step. Any error aborts the job with a structured
   message.
7. Runs the pipeline.
8. Collects the outputs from WBPP's `master/` directory, along with per-frame weights,
   rejected frames, and the reference frame from WBPP's process log. It writes all of this
   to `result.json`.
9. Wraps everything in `try/catch` so `result.json` is always written with
   `{status, error, stack}`.

**Fallback path (the design keeps this open):** if a given PixInsight or WBPP release
makes engine-level driving impractical, the wrapper switches to a
`native_pipeline` mode, selected by config. That mode calls the same underlying processes
directly, all of which have stable PJSR interfaces: `ImageCalibration`,
`CosmeticCorrection`, `Debayer`, `SubframeSelector` (weighting), `StarAlignment`,
`LocalNormalization`, `ImageIntegration`, `DrizzleIntegration`. The orchestrator's
contract (`job.json` in, `result.json` out) is the same either way, so the rest of the
system does not change.

#### 6.5.3 Calibration-master jobs

`CALIB_MASTER` jobs use the same runner in a "calibration only" mode. WBPP is given only
darks, bias, or flats (with their calibration masters), and its master outputs are
collected. The alternative is to call `ImageIntegration` directly, with the recommended
rejection settings for the frame count. The resulting master is:

- Renamed according to the calibration library convention (§4).
- Stamped with canonical FITS keywords (`IMAGETYP = 'Master Dark'`, `EXPTIME`, `GAIN`,
  `OFFSET`, `CCD-TEMP`, `INSTRUME`, `TELESCOP`, `FILTER`, `XBINNING`, and so on) so it can
  be re-indexed on its own.
- Registered in `calibration_masters`, and the previous master with the same match key is
  marked `superseded_by`.

The raw calibration subs are moved to `calibration/raw/…`.

### 6.6 Verifier & Publisher

After a `LIGHT_STACK` job succeeds:

1. **Sanity checks** on the master: it exists and opens; its dimensions are plausible
   (after autocrop, at least 70% of sensor area, configurable); its median and noise are
   not NaN or zero; the frame count used is at least `min_lights_per_stack`; and the
   fraction of frames rejected is at most 50% (warn above 25%).
2. **Stamp metadata** into the master's FITS keywords and XISF properties: canonical tuple,
   session date, `NCOMBINE`, total integration time, calibration master IDs and hashes, and
   `ALTAIRJB` (the job ID).
3. **Publish** to `masters/<target>/<session-date>/…` with the naming convention in §4,
   plus a JSON **provenance sidecar** holding the full `plan_json`, the per-frame weights
   and rejections, and software versions.
4. **Archive** the raw lights to `archive/…` (move, not copy, verified by hash after the
   move), and set `frames.status = 'archived'`.
5. **Clean up** `work/<job-id>/` if configured.

A failure in any step leaves the inputs untouched in the inbox, so every step is safe to
retry.

### 6.7 Notifier

One summary is sent per session, after all its jobs are finished:

```
Altair — session 2026-09-24: PARTIAL (3/4 stacks)
✔ M31  | esprit100/asi2600mm | Ha   | 42×300s (3h30m) | 3 rejected
✔ M31  | esprit100/asi2600mm | OIII | 40×300s (3h20m) | 1 rejected
✔ NGC7000 | rasa8/asi533mc   | OSC  | 180×60s (3h00m) | 12 rejected
✘ M31  | esprit100/asi2600mm | SII  | missing calibration: Flat (esprit100/asi2600mm/SII, rot 12°)
Built calibration: 1 master flat (Ha), 0 darks
```

Channels are pluggable: Pushover, ntfy, email (SMTP), Discord or Slack webhook.

---

## 7. Calibration Matching Rules

This is the part most likely to cause silent quality problems, so the rules are explicit
and unit-tested.

### 7.1 Match keys

| Master | Must match exactly | Must match within tolerance | Selection among candidates |
|---|---|---|---|
| **Dark** | camera, gain, offset, binning, readout_mode, sensor dimensions | exposure (±`exposure_tolerance_s`), sensor_temp (±`temp_tolerance_c`) | closest temp, then most recent, then highest `n_frames` |
| **Flat** | telescope, camera, filter, binning, sensor dimensions | rotator (±`rotator_tolerance_deg`, circular), age ≤ `max_age_days` | same session first (if `require_same_session_if_available`), then nearest in time, **preferring flats taken after the lights over flats taken before a known optical change** |
| **Bias** (for flats, or for lights if dark scaling is on) | camera, gain, offset, binning, readout_mode | age | most recent |
| **Dark-flat** | camera, gain, offset, binning, readout_mode | exposure ≈ flat exposure | closest exposure, then most recent |

### 7.2 Optical train changes

Flats are only valid while the optical train stays the same. An optional
`equipment_events` list in the config (or a table filled in from the CLI) records events
like `{date, telescope, camera, event: "cleaned sensor" | "rotated camera" | "reducer changed"}`.
A flat is **never** matched across an equipment event that falls between the flat's date
and the lights' dates.

### 7.3 Dark scaling / optimization

The default is **off**: CMOS amp glow does not scale linearly. If
`allow_exposure_scaling: true`, a dark with a different exposure can be used together with
a master bias, and WBPP's "optimize darks" is enabled. This is reported in the provenance
record.

### 7.4 OSC specifics

For OSC cameras: calibrate before debayering (WBPP does this), take the Bayer pattern from
the camera config if the header lacks `BAYERPAT`, and use `OSC` as the filter key unless a
per-rig `default_filter` is set.

### 7.5 No-match behavior

The system never falls back to "closest available" beyond the configured tolerances. A
missing match means the stack is skipped and a notification lists the exact frames needed.

---

## 8. Concurrency, Robustness & Idempotency

- **One PixInsight job at a time** by default (`max_concurrent_jobs`). The job queue is a
  table-backed FIFO. Jobs are ordered by dependency (calibration masters before the lights
  that use them), then by session date.
- **Crash recovery:** on startup, jobs left in `running` are reset to `queued`, their
  `work/` directories are cleared, and stale PixInsight slot locks are removed.
- **Retries:** transient failures (timeout, PixInsight crash with no `result.json`) are
  retried up to 2 times with backoff. Deterministic failures (validation errors) are not
  retried.
- **Disk space guard:** before a job starts, check that the free space in `work/` is at
  least `3 × (sum of input sizes) × drizzle_scale²`. If not, the job is deferred and a
  notification is sent.
- **Late frames:** a light that arrives for a session already `done` puts the session back
  in `collecting`. The planner then produces a new plan (with a different `plan_hash`),
  and the new master **supersedes** the old one. The old master is kept with a
  `.superseded` suffix.
- **Late calibration:** when new calibration masters are registered, any stacks in
  `skipped: missing_calibration` are re-planned automatically.
- **Never delete raw data.** Raw frames are moved only, and only after the output they fed
  has been verified.

---

## 9. Interfaces

### 9.1 CLI (`altair`)

```
altair serve                      # run the daemon (watcher + scheduler + executor)
altair ingest <path>...           # index files without triggering
altair status [--session DATE]    # sessions, jobs, stacks, missing calibration
altair plan --session DATE        # dry run: print the plan, calibration matches, and warnings
altair run --session DATE [--force] [--only target=M31,filter=Ha]
altair calib list|import <dir>|build --session DATE
altair reprocess --job ID         # re-run with the current profile
altair doctor                     # check config, PixInsight path, CLI flags, Xvfb, disk, DB
```

### 9.2 Optional HTTP status endpoint

A read-only `GET /status` and `GET /sessions/<date>` endpoint (FastAPI, bound to
localhost by default), for dashboards or Home Assistant.

### 9.3 Capture-side hook

Example NINA end-of-sequence command:

```
powershell -Command "New-Item -Path '\\nas\astro\inbox\SESSION_COMPLETE' -ItemType File -Force"
```

---

## 10. Observability

- Structured JSON logs (one line per event, with session, job, and stack key).
- A full PixInsight console log per job, kept for 90 days.
- A per-session HTML/Markdown report in `masters/<target>/<session>/report.md`: frames
  used and rejected, weights, FWHM and eccentricity summary from WBPP, calibration used,
  timings.
- Metrics (optional, Prometheus text format): job durations, frames processed, rejection
  rate, queue depth.

---

## 11. Future Work (explicitly out of v1)

- **Cumulative masters:** re-integrate all archived, calibrated lights for a tuple across
  every night (optionally within a date window). This needs `save_calibrated_frames: true`
  and a registered-frame cache.
- **Multi-rig combined projects:** combining the same target and filter from different
  telescopes (needs cross-rig registration and resampling).
- Frame-level pre-screening before WBPP (clouds, satellite-dense frames, guiding failures),
  using quick-look statistics or the capture software's HFR and star-count metadata.
- GPU or distributed execution; a containerized PixInsight worker.
- Automatic hand-off to a post-processing script (for example BlurXTerminator or
  GraXpert) as a separate, optional stage.

---

## 12. Implementation Plan & Milestones

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **0: PixInsight headless spike** | A minimal PJSR script run by the CLI under Xvfb that (a) opens and saves an image, and (b) drives WBPP's engine on a small hand-picked dataset with no dialog. Decision recorded: WBPP-engine mode or native-pipeline fallback. | A master light is produced by a single shell command with no UI interaction, and the working CLI flags are documented in `docs/pixinsight-cli.md`. |
| **1: Catalog & normalization** | Config loading, header mapping and aliases, the ingest pipeline, the SQLite schema, and `altair ingest` / `altair status`. | Real sample sessions from each rig index correctly. Unit tests cover NINA, SGP, and Ekos header variants. |
| **2: Planner & calibration matching** | Grouping, the matching rules in §7, `altair plan` (dry run). | Table-driven tests for every rule in §7, including tolerance edges, equipment events, and no-match. |
| **3: Executor & WBPP runner** | `altair run` end to end for one session, `job.json` / `result.json` contract, timeouts, and process cleanup. | Produces masters identical (within noise) to a manual WBPP run on the same data and settings. |
| **4: Calibration library automation** | CALIB_MASTER jobs, library naming and stamping, supersession, `altair calib import` for existing masters. | A session containing only flats produces registered master flats that are matched automatically on the next night. |
| **5: Triggers & daemon** | Watcher, sentinel, quiescence plus dawn, scheduled fallback, `altair serve`, systemd unit, crash recovery. | Unattended run: files copied into the inbox overnight lead to masters and a notification by morning. Survives a kill -9 mid-job. |
| **6: Publishing, notifications, reports** | Verifier, archive moves, sidecars, notifier channels, per-session report. | A full week of real sessions processed unattended with correct outputs. |

### 12.1 Proposed source layout

```
altair-pre-processor/
├── pyproject.toml
├── altair.example.yaml
├── src/altair/
│   ├── cli.py
│   ├── config.py            # pydantic models
│   ├── daemon.py            # service loop, scheduler
│   ├── triggers/            # watcher.py, sentinel.py, schedule.py, dawn.py
│   ├── ingest/              # headers.py (FITS/XISF), normalize.py, classify.py
│   ├── catalog/             # db.py, models.py, migrations/
│   ├── planner/             # grouping.py, matching.py, plan.py
│   ├── executor/            # pixinsight.py (process mgmt), jobs.py (queue)
│   ├── publish/             # verify.py, stamp.py, archive.py, report.py
│   └── notify/              # base.py, pushover.py, ntfy.py, email.py, webhook.py
├── pjsr/
│   ├── altair_wbpp_runner.js
│   ├── altair_native_pipeline.js
│   └── lib/json_io.js
├── deploy/
│   ├── altaird.service      # systemd
│   └── windows/             # NSSM instructions
├── tests/
│   ├── fixtures/            # synthetic FITS generated with astropy (tiny, e.g. 64×64)
│   ├── test_normalize.py
│   ├── test_matching.py
│   ├── test_planner.py
│   └── test_executor_contract.py   # uses a fake PixInsight that writes result.json
└── docs/
    ├── SPEC.md
    └── pixinsight-cli.md
```

### 12.2 Testing strategy

- **Unit:** normalization, classification, session-date rollover (including DST changes),
  and every matching rule. These use synthetic headers only.
- **Contract:** a fake `PixInsight.sh` that validates `job.json` and writes a canned
  `result.json` / master. This lets the orchestrator be tested in CI without PixInsight.
- **Integration (local only, marked):** real PixInsight on a small reference dataset (for
  example 10 lights, 15 darks, 15 flats, 15 dark-flats at 2×2 binning or cropped), with
  golden-master comparison by statistics (median, MAD, and star count within tolerance),
  not by bytes.
- **Soak:** replay a recorded night's file arrivals into the inbox at real-time or 10×
  speed.

---

## 13. Risks & Open Questions

| # | Item | Mitigation / decision needed |
|---|---|---|
| R1 | **WBPP is not officially a headless API.** Its internal engine can change between releases. | Pin the tested PixInsight and WBPP versions. `altair doctor` checks the version. Keep the native-pipeline fallback (§6.5.2) behind the same contract. |
| R2 | PixInsight needs a display, even with `--automation-mode`. | Xvfb on Linux. On Windows or macOS, run under a logged-in service user. Phase 0 confirms this. |
| R3 | PixInsight licensing on a separate processing host. | Confirm the license covers the processing machine. |
| R4 | Header quality varies between capture programs (OBJECT missing on flats, filter-name drift). | Alias maps, `altair status` surfaces invalid frames, frames are held rather than dropped. |
| R5 | Network share file events are unreliable. | Stability checks, the scheduled fallback, and a periodic rescan. |
| R6 | Large sessions (OSC with drizzle) can exhaust RAM or disk. | Disk guard, one concurrent job, drizzle off by default. |
| Q1 | Should masters be **per-night only**, or also **cumulative across nights** for a tuple? | v1: per-night. Cumulative is Future Work (§11). |
| Q2 | Which capture software(s) and header conventions are in use? | Needed to fill in `header_mapping` defaults. |
| Q3 | Processing host OS (Linux vs Windows)? | Changes the deployment and display strategy (R2). |
| Q4 | Output format: XISF only, or also FITS? | Default XISF. Optional FITS export for other tools. |
| Q5 | Should lights with no matching flat be integrated without flat calibration (clearly labelled), or skipped? | v1 default: skip and notify (§7.5). Could be a per-rig opt-in. |
