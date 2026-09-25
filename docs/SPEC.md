# Altair Pre-Processor — Implementation Specification

**Status:** Draft v0.2
**Date:** 2026-09-25
**Target platform:** Windows 10/11 (x64), NINA for acquisition, PixInsight 1.9.x with WBPP 2.x

### Changelog

| Version | Changes |
|---|---|
| v0.2 | Windows and NINA are now the target platform. Adds multi-night masters that reuse registration and weight each night by its measured quality. Flat matching now covers equipment and rotator position, including rotator step position. Adds an issue and alert system with automatic rerun once the problem is fixed. |
| v0.1 | First draft. |

---

## 1. Purpose

Altair Pre-Processor is a fully automated, event-triggered pipeline. It takes the raw
frames from a night's NINA imaging session and turns them into calibrated, registered,
integrated master lights, with no human in the loop.

It produces two kinds of output:

1. **Night masters:** one master light for each **(telescope, camera, filter, target)**
   tuple captured that night. Each is calibrated with the correct darks, flats, and bias or
   dark-flat frames for that equipment configuration and rotator position.
2. **Multi-night masters (optional, per target):** a running master for each tuple that
   combines every eligible night. Adding a night does **not** repeat registration: each
   night is registered once, to a fixed project reference frame. Each night's
   contribution is weighted by its **measured** quality.

A night is **never merged** unless every one of its lights has a verified matching flat
(same equipment, same filter, same rotator position). When a flat is missing, the pipeline
raises an **issue** saying exactly which flats are needed. It then watches for them and
reruns and merges that night automatically once they show up.

Calibration, registration, and per-night integration are done by **PixInsight's
WeightedBatchPreprocessing (WBPP)** script, driven headlessly. Multi-night merges use
PixInsight's `ImageIntegration` and `LocalNormalization` processes directly.

### 1.1 Goals

- **Zero-touch:** after a NINA sequence ends, the night masters and updated multi-night
  masters are ready by morning.
- **Correct calibration matching or no merge:** a light is never quietly calibrated with
  the wrong flat, and a night whose calibration is incomplete is never merged.
- **Register once:** a night's frames are registered exactly once, into the project's
  fixed geometry. Merging never re-registers anything.
- **Measured weighting:** each night's weight in a multi-night master comes from image
  measurements, not from frame counts.
- **Recoverable:** every blocking problem becomes an issue that names the fix. Once the fix
  arrives, the rerun is automatic.
- **Deterministic, idempotent, auditable:** the same inputs give the same outputs, and every
  master traces back to its exact lights, calibration masters, weights, settings, and
  software versions.

### 1.2 Non-goals (v1)

- Post-processing (gradient removal, color calibration, stretching, deconvolution).
- Acquisition control. The pipeline only reads what NINA writes. It may *generate* helper
  files for NINA (§10.4) but never drives the equipment.
- A GUI. Configuration is YAML. Status comes through notifications, a generated issues
  and status page, and the CLI.
- Combining data from different telescopes or cameras into one master.

---

## 2. Terminology

| Term | Meaning |
|---|---|
| **Light / Dark / Flat / Bias / Dark-flat** | The usual frame types. NINA writes them as `IMAGETYP` = `LIGHT`, `DARK`, `FLAT`, `BIAS`, `DARKFLAT`. |
| **Rig** | A configured (telescope, camera) pair, with its focal length, optional rotator, and filter set. |
| **Optical train signature** | Everything that changes where vignetting and dust shadows fall: telescope, camera, focal length (reducer or flattener), binning, sensor ROI, and rotator position. |
| **Night** | All frames from one observing night, defined as the local noon-to-noon window. This matches NINA's `$$DATEMINUS12$$` token. |
| **Stack key** | `(telescope, camera, filter, target)`. |
| **Project** | `(target, telescope, camera)`. Holds one project reference frame and one multi-night master per filter. |
| **Project reference** | The single calibrated frame whose geometry every night of the project is registered to, across **all filters**. |
| **Night master** | The integrated master for one stack key on one night, in project reference geometry. |
| **Multi-night master** | The weighted combination of every eligible night master for one stack key. |
| **Eligible night** | A night master that passes every merge gate in §9.4, including verified flat calibration. |
| **Issue** | A stored, trackable problem (for example `FLAT_MISSING`) that blocks a stack or a merge. It carries fix instructions and a status of open, resolved, or waived. |

---

## 3. High-Level Architecture

```
           ┌───────────────────────────────────────────────────────────────────────┐
           │                            altaird (Windows)                           │
 NINA ───► │ 1 Trigger ─► 2 Ingest & ─► 3 Planner ─► 4 Executor ─► 5 Verifier &    │
 (images + │   Detector     Catalog      (group,      (PixInsight   Publisher       │
 end-of-   │   (sentinel,   (FITS/XISF    calib/flat   WBPP, per     (night masters)│
 sequence  │   quiescence,  headers →     matching)    night)             │         │
 script)   │   schedule)    SQLite)          │                            ▼         │
           │                                 │                     6 Merger        │
           │   Calibration Library ◄─────────┤                     (ImageIntegration│
           │                                 │                      over night     │
           │                                 ▼                      masters)       │
           │                          7 Issue Tracker ◄──── gates ──────┘          │
           │                                 │   ▲                                 │
           │                                 │   └── new calibration → auto-rerun  │
           │                                 ▼                                     │
           │                          8 Notifier (toast, push, email, status page) │
           └───────────────────────────────────────────────────────────────────────┘
```

The **orchestrator** (`altaird`) is a long-running Python service. PixInsight is an
external worker that the orchestrator starts once per job, as a separate process.

### 3.1 Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Orchestrator | Python 3.12+, shipped as a venv or a PyInstaller-built `altair.exe` | Mature FITS tooling, easy subprocess and Win32 control. |
| Header I/O | `astropy.io.fits`, plus a small XISF header reader | NINA can save FITS or XISF. |
| File watching | `watchdog` (uses `ReadDirectoryChangesW`), plus a periodic rescan | Reacts quickly, and the rescan catches events that were missed. |
| Win32 integration | `pywin32` / `psutil` | Job Objects for killing the PixInsight process tree, `SetThreadExecutionState` to keep the PC awake, exclusive-open checks, toast notifications. |
| State store | SQLite (WAL mode) | Single host, transactional, needs no server. |
| Config | YAML validated with `pydantic` | Typed, and bad config fails at startup. |
| Job execution | PixInsight CLI in automation mode running PJSR wrapper scripts | Required by the project goal. |

---

## 4. Windows & NINA Environment

### 4.1 Deployment model

PixInsight is a Qt GUI application. Even in `--automation-mode` it needs an **interactive
desktop session**. Windows services run in session 0, which has no desktop, so PixInsight
started from a service may fail or hang. So:

- `altaird` runs **in the user's interactive session**, started by a **Task Scheduler**
  task with the trigger *At log on* and *Run only when user is logged on*, launched with
  `pythonw.exe` / `altair.exe serve --windowless` so no console window appears.
- On a dedicated processing or imaging PC, enable auto-logon for the observatory user. A
  **locked** workstation is fine: processes keep running in a locked session. Logging out
  is not fine.
- The Task Scheduler task is set to restart on failure (every 1 min, up to 999 times). This
  is the watchdog for crashes.
- `altair doctor` checks that it is running in an interactive session (not session 0) and
  warns if it isn't.

**Same PC as NINA:** processing never starts while a NINA sequence is active. It is gated
on the session-end signal (§6.1) and on NINA having written no new frames for the
quiescence window. PixInsight runs at `BELOW_NORMAL_PRIORITY_CLASS`. If a new NINA frame
lands while a job is running (for example a new sequence starts), the running job
finishes, and no further jobs start until the next session end.

**Keeping the PC awake:** while any job is queued or running, `altaird` calls
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`. `altair doctor` warns if
Windows Update active hours overlap the configured processing window.

**Process control:** each PixInsight run is assigned to a **Windows Job Object** with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. On timeout or crash, the whole process tree is killed
reliably and no orphaned `PixInsight.exe` is left holding the instance slot.

**Performance notes (checked by `altair doctor`):** PixInsight swap directories should be
on a fast SSD. The `work` and `projects` directories should be excluded from Windows
Defender real-time scanning. Long-path support should be enabled
(`LongPathsEnabled=1`), because WBPP output paths get deep.

### 4.2 NINA integration

**Session-end signal.** In the NINA Advanced Sequencer, add an **External Script**
instruction to the sequence's *End* area (and optionally after each target's flats):

```
"C:\Program Files\Altair\altair.exe" signal session-end --source nina
```

This writes an atomic `SESSION_COMPLETE.json` into the inbox, recording the time and host.
If the sequence aborts or NINA crashes, quiescence and the scheduled fallback (§6.1) still
catch the session.

**Recommended NINA image file pattern** (Options → Imaging). It is not required, because
Altair relies on headers, but it keeps the inbox readable:

```
$$DATEMINUS12$$\$$TARGETNAME$$\$$IMAGETYPE$$\$$FILTER$$\$$DATETIME$$_$$FILTER$$_$$EXPOSURETIME$$s_$$FRAMENR$$
```

**Flats in NINA.** Rotator-aware flat matching (§8) works best when flats are taken **at
the same rotator position as the lights**, before the rotator moves. For example, put a
*Trained Flat Exposure* instruction per filter at the end of each target block, before the
next target's rotator move.

**NINA FITS keywords used** (the defaults in `header_mapping`; Phase 0 confirms them
against sample files from your NINA version):

| Canonical field | NINA keyword(s) |
|---|---|
| image_type | `IMAGETYP` |
| telescope | `TELESCOP` |
| camera | `INSTRUME` |
| focal_length | `FOCALLEN` |
| filter | `FILTER` |
| target | `OBJECT` |
| exposure | `EXPOSURE`, `EXPTIME` |
| gain / offset | `GAIN`, `OFFSET` |
| sensor temp | `CCD-TEMP` (actual), `SET-TEMP` (set point) |
| binning | `XBINNING`, `YBINNING` |
| readout mode | `READOUTM` |
| Bayer pattern | `BAYERPAT` |
| rotator (mechanical) | `ROTATOR` (mechanical position), `ROTATANG` |
| date | `DATE-OBS` (UTC), `DATE-LOC` |
| pointing | `RA`, `DEC`, `OBJCTRA`, `OBJCTDEC` |

If a rotator reports **step position** instead of degrees, or the value only appears in
the file name, the rig's rotator config (§5) says which keyword or file-name token to read
and how to interpret it.

---

## 5. Configuration

A single `altair.yaml` (default `C:\ProgramData\Altair\altair.yaml`), validated at startup.

```yaml
site:
  name: "Backyard Observatory"
  latitude: 37.3
  longitude: -121.9
  timezone: "America/Los_Angeles"
  session_rollover_local: "12:00"

paths:
  inbox: "D:/Astro/NINA"                  # NINA's image save root (or a synced share)
  work: "E:/AltairWork"                   # fast SSD scratch space
  calibration: "D:/Astro/Calibration"
  projects: "D:/Astro/Projects"           # project references, night masters, multi-night masters
  archive: "D:/Astro/Archive"
  published: "D:/Astro/Masters"           # user-facing copies of night and multi-night masters
  rejected: "D:/Astro/Rejected"
  state: "C:/ProgramData/Altair/state"

pixinsight:
  executable: "C:/Program Files/PixInsight/bin/PixInsight.exe"
  wbpp_dir: "C:/Program Files/PixInsight/src/scripts/BatchProcessing/WeightedBatchPreprocessing"
  instance_slot: 5
  extra_args: ["--automation-mode", "--no-startup-scripts", "--force-exit"]
  priority: below_normal
  timeout_minutes: 360
  max_concurrent_jobs: 1
  tested_versions: ["1.9.3"]              # altair doctor warns on any other version

triggers:
  sentinel_filename: "SESSION_COMPLETE.json"
  quiescence_minutes: 45
  require_after_dawn: true
  scheduled_fallback_local: "09:00"
  rescan_interval_minutes: 15
  min_lights_per_stack: 5

rigs:
  esprit100_2600mm:
    telescope: "esprit100"
    camera: "asi2600mm"
    focal_length_mm: 550
    focal_length_tolerance_mm: 10          # detects reducer/flattener changes via FOCALLEN
    rotator:
      present: true
      source: header                       # header | filename
      keywords: ["ROTATOR"]                # first keyword present wins
      # filename_regex: "_ROT(?P<pos>-?\\d+(\\.\\d+)?)_"   # used when source: filename
      units: steps                         # degrees | steps
      steps_per_revolution: 108000         # omit if unknown → linear compare, no wrap
      tolerance: 50                        # in `units`
      require_on_lights_and_flats: true    # missing value → cannot prove match → issue
  rasa8_533mc:
    telescope: "rasa8"
    camera: "asi533mc"
    focal_length_mm: 400
    rotator: { present: false }
    default_filter: "OSC"

aliases:                                   # raw header value (regex) → canonical name
  telescope: { "Esprit 100ED|ESPRIT100": esprit100, "RASA 8": rasa8 }
  camera:    { "ZWO ASI2600MM Pro": asi2600mm, "ZWO ASI533MC Pro": asi533mc }
  filter:    { "^(Ha|H-alpha|HA)$": Ha, "^(OIII|O3)$": OIII, "^(SII|S2)$": SII, "^(L|Lum)$": L }
  target:    { "^(M 31|M31|Andromeda Galaxy)$": M31 }

cameras:
  asi2600mm: { type: mono, cooled: true }
  asi533mc:  { type: osc,  cooled: true, bayer_pattern: RGGB }

calibration_matching:
  dark:     { exposure_tolerance_s: 0.0, temp_tolerance_c: 2.0, max_age_days: 180 }
  bias:     { max_age_days: 365 }
  darkflat: { exposure_tolerance_s: 0.1, max_age_days: 180 }
  flat:
    max_age_days: 60                     # either side of the night; equipment events still apply
    prefer: [same_night, nearest_after, nearest_before]
  flat_dark_strategy: darkflat_preferred   # darkflat_preferred | bias_only | darkflat_only

night_processing:
  produce_provisional_without_flat: true   # makes a "_NOFLAT" preview master; never merged
  wbpp_profile: default

wbpp:
  profiles:
    default:
      cosmetic_correction: { enabled: true, mode: auto_detect, hot_sigma: 3.0, cold_sigma: 3.0 }
      debayer: { method: VNG }
      subframe_weighting: PSF_SIGNAL_WEIGHT
      minimum_weight: 0.05
      registration:
        reference: project                # project reference (§9.2); never "auto" once a project exists
        distortion_correction: false
      local_normalization: true
      integration: { rejection: auto }
      drizzle: { enabled: false, scale: 1 }
      autocrop: false                     # MUST stay false: night masters keep project geometry (§9.3)

multi_night:
  enabled: true
  mode: master_merge                      # master_merge (default) | frame_reintegration (§9.6)
  night_weighting: measured_psf_signal    # measured_psf_signal | inverse_noise_variance | frame_weight_sum
  normalization: local                    # local | global
  rejection: none                         # none | winsorized_sigma (only when nights >= 8)
  min_nights: 1
  max_fwhm_ratio_to_project_median: 1.6   # QA gate; null disables it
  min_overlap_fraction: 0.8               # with the project reference
  rebuild: from_all_nights                # always rebuilt from scratch; see §9.5
  keep_versions: 10
  autocrop_output: true

issues:
  page: "D:/Astro/Masters/ALTAIR_STATUS.html"
  remind_every_days: 3
  auto_rerun_on_resolution: true

notifications:
  on: [success, partial, failure, issue_opened, issue_resolved]
  channels:
    - type: windows_toast
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

This component decides when a night is ready to process. Any of these triggers can fire,
and they are debounced into a single "night ready" event:

1. **NINA sentinel.** `SESSION_COMPLETE.json` is written by the NINA External Script
   (§4.2). Altair reacts immediately.
2. **Quiescence.** No new light for `quiescence_minutes`, *and* the local time is after
   astronomical dawn (if `require_after_dawn`). Dawn is computed with `astropy` from the
   site coordinates. This catches aborted sequences.
3. **Scheduled fallback.** A daily run at `scheduled_fallback_local`, plus a periodic
   rescan of the inbox. This covers missed file events, which are common on SMB shares.
4. **Calibration arrival.** Newly indexed calibration frames or masters that could resolve
   an open issue (§10) trigger a targeted re-plan.
5. **Manual.** `altair run …` / `altair rerun …`.

**File stability:** a file is ingested only when (a) its size and mtime have not changed
for 30 s, (b) it can be opened with **exclusive share mode** (so NINA has finished writing
it), and (c) it parses as valid FITS or XISF.

### 6.2 Ingest & Catalog

For each stable file:

1. Read the headers and resolve canonical fields through `header_mapping` and `aliases`.
2. Resolve the **rig** from (telescope, camera). Frames that match no configured rig get
   an `UNKNOWN_RIG` issue.
3. Extract the **rotator position** using the rig's rotator config: read the keyword or
   file-name token, parse it as a number in `units`, and store it as `rotator_pos`
   together with `rotator_units`.
4. Classify the image type from NINA's `IMAGETYP` values.
5. Compute `night` (noon-to-noon local time) and a content hash (xxhash64).
6. Store the row in `frames`. Frames missing required fields are `invalid` and get an issue
   (`HEADER_INCOMPLETE`). They stay in the inbox so you can fix the headers or add aliases,
   and `altair rerun` picks them up.

**Required fields:**

| Type | Required |
|---|---|
| LIGHT | telescope, camera, filter\*, target, exposure, gain\*\*, binning, focal_length, date_obs, rotator_pos\*\*\* |
| FLAT | telescope, camera, filter\*, exposure, binning, focal_length, date_obs, rotator_pos\*\*\* |
| DARK / DARKFLAT | camera, exposure, gain\*\*, offset\*\*, binning, sensor_temp (if cooled) |
| BIAS | camera, gain\*\*, offset\*\*, binning |

\* OSC rigs fall back to `default_filter`.
\*\* For cameras that report gain and offset.
\*\*\* Only when the rig has `rotator.present: true` and `require_on_lights_and_flats: true`.

### 6.3 Data Model (SQLite)

```sql
CREATE TABLE frames (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL, content_hash TEXT NOT NULL,
  image_type TEXT NOT NULL, night TEXT NOT NULL, date_obs TEXT NOT NULL,
  rig TEXT, telescope TEXT, camera TEXT, filter TEXT, target TEXT,
  focal_length REAL, exposure REAL, gain INTEGER, offset INTEGER, sensor_temp REAL,
  binning TEXT, readout_mode TEXT, width INTEGER, height INTEGER, bayer_pattern TEXT,
  rotator_pos REAL, rotator_units TEXT,
  raw_headers_json TEXT,
  status TEXT NOT NULL,          -- new/valid/invalid/held/processed/archived/rejected
  status_reason TEXT
);

CREATE TABLE calibration_masters (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,            -- DARK/FLAT/BIAS/DARKFLAT
  path TEXT UNIQUE NOT NULL, content_hash TEXT NOT NULL,
  camera TEXT NOT NULL, telescope TEXT, filter TEXT, focal_length REAL,
  exposure REAL, gain INTEGER, offset INTEGER, sensor_temp REAL,
  binning TEXT, readout_mode TEXT, width INTEGER, height INTEGER,
  rotator_pos REAL, rotator_units TEXT,
  night TEXT, n_frames INTEGER,
  superseded_by INTEGER REFERENCES calibration_masters(id),
  quality_json TEXT
);

CREATE TABLE equipment_events (   -- flats are never matched across one of these
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL, rig TEXT NOT NULL,
  kind TEXT NOT NULL,             -- sensor_cleaned / filter_changed / camera_rotated_manually / reducer_changed / collimated / other
  filter TEXT,                    -- null = all filters
  note TEXT
);

CREATE TABLE projects (
  id INTEGER PRIMARY KEY,
  target TEXT NOT NULL, telescope TEXT NOT NULL, camera TEXT NOT NULL,
  reference_path TEXT, reference_hash TEXT,
  reference_night TEXT, reference_version INTEGER NOT NULL DEFAULT 1,
  pixel_scale_arcsec REAL, drizzle_scale INTEGER NOT NULL DEFAULT 1,
  UNIQUE(target, telescope, camera)
);

CREATE TABLE night_masters (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  night TEXT NOT NULL, filter TEXT NOT NULL,
  path TEXT NOT NULL, content_hash TEXT NOT NULL,
  kind TEXT NOT NULL,             -- final / provisional_noflat
  reference_version INTEGER NOT NULL,
  n_frames INTEGER, n_rejected INTEGER, total_exposure_s REAL,
  calib_json TEXT NOT NULL,       -- dark/flat/bias/darkflat ids + match evidence per light group
  flat_verified INTEGER NOT NULL, -- 1 only if every light had a matching flat (§8)
  metrics_json TEXT,              -- median FWHM, eccentricity, noise, PSF signal, star count, overlap
  night_weight REAL,              -- last measured weight (§9.5); informational
  merge_status TEXT NOT NULL,     -- eligible / blocked / excluded
  merge_block_reason TEXT,
  job_id INTEGER REFERENCES jobs(id),
  superseded_by INTEGER REFERENCES night_masters(id),
  UNIQUE(project_id, night, filter, kind, reference_version, content_hash)
);

CREATE TABLE multi_night_masters (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  filter TEXT NOT NULL, version INTEGER NOT NULL,
  path TEXT NOT NULL,
  inputs_json TEXT NOT NULL,      -- [{night_master_id, weight, weight_fraction}]
  excluded_json TEXT NOT NULL,    -- [{night, reason, issue_id}]
  total_exposure_s REAL, n_nights INTEGER,
  plan_hash TEXT UNIQUE NOT NULL, created_at TEXT
);

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,             -- CALIB_MASTER / NIGHT_STACK / PROJECT_REFERENCE / MERGE
  scope_json TEXT NOT NULL,
  plan_json TEXT NOT NULL, plan_hash TEXT UNIQUE NOT NULL,
  depends_on_json TEXT,
  status TEXT NOT NULL,           -- queued/running/succeeded/failed/skipped/blocked
  attempts INTEGER DEFAULT 0,
  started_at TEXT, finished_at TEXT, log_path TEXT, error TEXT
);

CREATE TABLE issues (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,             -- see §10.1
  severity TEXT NOT NULL,         -- blocking / warning
  status TEXT NOT NULL,           -- open / resolved / waived
  fingerprint TEXT UNIQUE NOT NULL,  -- dedupes the same problem across re-plans
  scope_json TEXT NOT NULL,       -- rig, night, target, filter, frame ids
  requirement_json TEXT,          -- machine-readable description of what would fix it
  message TEXT NOT NULL,          -- human-readable description + fix instructions
  created_at TEXT, last_notified_at TEXT,
  resolved_at TEXT, resolution TEXT,  -- auto:<calib_master_id> / manual:<note> / waived:<note>
  rerun_job_ids_json TEXT
);
```

### 6.4 Planner

The planner runs when a night is ready, and again whenever an issue might be resolvable.

1. **Calibration masters.** Group the night's raw calibration subs (dark, bias, and
   dark-flat by sensor settings; flats by optical train signature plus filter plus
   `rotator_pos` bucket) into `CALIB_MASTER` jobs. They are ordered bias/dark-flat →
   dark → flat.
2. **Light groups.** Group lights by stack key, then split by exposure, gain, offset,
   binning, and **rotator position**. Each rotator position needs its own flat. Groups at
   different rotator positions but the same stack key still integrate into **one** night
   master. They are calibrated separately and then registered to the same project
   reference.
3. **Calibration matching** (§8), per light group. The result is either a full calibration
   plan or a set of blocking **issues** (for example `FLAT_MISSING`).
4. **Stack decision:**
   - Every group fully calibrated → `NIGHT_STACK` job, `kind = final`.
   - Any group missing a flat → the night master is **blocked** for that stack key. If
     `produce_provisional_without_flat` is on, a `provisional_noflat` night master is
     built for preview (clearly labeled, and never eligible for merging). Raw lights are
     `held`: they are archived, but tracked as needing reprocessing.
   - Missing dark or bias/dark-flat → blocked. No provisional master. The issue is raised.
5. **Project reference.** If the project has no reference yet, a `PROJECT_REFERENCE` job
   runs before the first `NIGHT_STACK` (§9.2).
6. **Merge.** After a night's stacks finish, a `MERGE` job is emitted for each affected
   (project, filter).
7. **Idempotency.** `plan_hash` is the SHA-256 of the canonical plan JSON: input hashes,
   calibration hashes, resolved settings, reference version, and software versions. A plan
   whose `plan_hash` already succeeded is skipped.

### 6.5 Executor (PixInsight on Windows)

Invocation, one process per job:

```
"C:\Program Files\PixInsight\bin\PixInsight.exe" -n=5 --automation-mode --no-startup-scripts --force-exit ^
    -r="C:\Program Files\Altair\pjsr\altair_runner.js,E:\AltairWork\<job-id>\job.json"
```

- The process is started with `CREATE_NO_WINDOW` where possible, at `BELOW_NORMAL`
  priority, and inside a Job Object (§4.1). It has a hard timeout.
- The PJSR runner reads `job.json` from `jsArguments[0]`, dispatches on `job.kind`, and
  **always** writes `result.json`, including from its catch blocks. A missing
  `result.json` or a non-zero exit means the job failed.
- Console output is captured to `state\logs\jobs\<job-id>.log`.
- On timeout: the Job Object is closed (which kills the whole process tree), stale
  instance-slot lock files are removed, and the job is retried once.

> **Phase 0 verification:** exact CLI flag spelling and behaviour, whether `-r` arguments
> reach the script through `jsArguments` on Windows, and whether an automation-mode
> instance can run while the user has an interactive PixInsight open on another slot.

**PJSR runner modes:**

| `job.kind` | What the runner does |
|---|---|
| `CALIB_MASTER` | WBPP in calibration-only mode (or `ImageIntegration` directly). Stamps canonical keywords, including `ROTATOR`, `FOCALLEN`, and `IMAGETYP='Master Flat'`. |
| `PROJECT_REFERENCE` | Picks and saves the project reference frame (§9.2). |
| `NIGHT_STACK` | Drives the WBPP engine headlessly. It loads WBPP's engine sources, applies the profile, and adds the lights and the **pre-built** masters (dark, flat, bias/dark-flat) as masters. It sets the registration reference to the **project reference file** (manual mode) with **autocrop off**, sets grouping keywords `TELESCOP, INSTRUME, FILTER, OBJECT, ROTATOR` as a safety net, runs WBPP, then collects the master, per-frame weights and measurements, and rejections. |
| `MERGE` | Measures the night masters, normalizes them, and integrates them with keyword weights (§9.5). |

**Fallback:** if engine-level WBPP driving turns out to be fragile on the installed
version, `NIGHT_STACK` switches (by config) to a native pipeline: `ImageCalibration` →
`CosmeticCorrection` → `Debayer` → `SubframeSelector` → `StarAlignment` (to the project
reference) → `LocalNormalization` → `ImageIntegration`. The `job.json` / `result.json`
contract does not change.

### 6.6 Verifier & Publisher

After a `NIGHT_STACK` job:

1. **Sanity checks:** the master opens, and its median, noise, and star count are sane.
   The used frame count is at least `min_lights_per_stack`, and the rejection fraction is
   at most 50%. **Overlap** with the project reference is at least `min_overlap_fraction`,
   measured from the StarAlignment coverage or the non-zero pixel fraction.
2. **Store the canonical copy** (uncropped, project geometry) under
   `projects\<project>\nights\<night>\<filter>\`, together with `night.json`, which holds
   the calibration evidence, metrics, per-frame weights, and rejections.
3. **Publish a viewing copy** (autocropped) to
   `published\<target>\<night>\<target>_<telescope>_<camera>_<filter>_<night>_<N>x<exp>s.xisf`.
   Provisional masters get the suffix `_NOFLAT-PROVISIONAL`.
4. **Move raw lights** to `archive\<target>\<telescope>_<camera>\<filter>\<night>\`, and
   verify the hashes after the move.
5. **Record the night master** with `merge_status`, set by the gates in §9.4.

### 6.7 Merger

See §9.

### 6.8 Issue Tracker & Notifier

See §10.

---

## 7. Directory Layout

```
D:\Astro\
├── NINA\                                  # inbox (NINA save root)
├── Calibration\
│   ├── raw\...
│   └── masters\
│       ├── darks\<camera>\G<gain>_O<offset>_<temp>C_<exp>s_bin<b>_<night>.xisf
│       ├── bias\<camera>\...
│       ├── darkflats\<camera>\...
│       └── flats\<telescope>\<camera>\<filter>\<night>_FL<focal>_ROT<pos><units>.xisf
├── Projects\
│   └── <target>__<telescope>__<camera>\
│       ├── project.json
│       ├── reference\reference_v<N>.xisf         # project reference frame
│       ├── nights\<night>\<filter>\
│       │   ├── night_master.xisf                 # canonical, uncropped, project geometry
│       │   ├── night_master_NOFLAT-PROVISIONAL.xisf
│       │   ├── night.json
│       │   └── frames\                           # only in frame_reintegration mode (§9.6)
│       └── multinight\<filter>\
│           ├── <target>_<filter>_multinight_v<NNN>.xisf
│           └── <same>.json
├── Masters\                                      # published, user-facing
│   ├── <target>\<night>\...                      # night masters (autocropped)
│   ├── <target>\multinight\<target>_<telescope>_<camera>_<filter>_<N>nights_<hours>h.xisf
│   └── ALTAIR_STATUS.html                        # issues and status page (§10.3)
├── Archive\...
└── Rejected\...
E:\AltairWork\<job-id>\                            # scratch, deleted on success
C:\ProgramData\Altair\{altair.yaml, state\altair.db, state\logs\}
```

---

## 8. Calibration Matching Rules

### 8.1 Match keys

| Master | Must match exactly | Within tolerance | Selection |
|---|---|---|---|
| **Dark** | camera, gain, offset, binning, readout mode, width×height | exposure, sensor temp | closest temp → most recent → most frames |
| **Flat** | rig (telescope + camera), **filter**, binning, width×height | **focal length** (±`focal_length_tolerance_mm`), **rotator position** (±`rotator.tolerance`, §8.2), age (±`max_age_days`) | order in `prefer` (same night → nearest after → nearest before); never across an equipment event (§8.3) |
| **Dark-flat** | camera, gain, offset, binning, readout mode | exposure ≈ flat exposure | closest exposure → most recent |
| **Bias** | camera, gain, offset, binning, readout mode | age | most recent |

A flat is calibrated with its own dark-flat or bias when its master is built. A light
uses dark + flat, and (with `bias_only` / dark scaling) a bias.

### 8.2 Rotator position matching

- The position is read per frame, using the rig's `rotator` config: the header keyword(s)
  or a file-name regex group, in `degrees` or `steps`.
- **Comparison:**
  - `degrees`: circular, `Δ = min(|a−b| mod 360, 360 − |a−b| mod 360)`.
  - `steps` with `steps_per_revolution` known: circular modulo steps-per-revolution.
  - `steps` without it: linear `|a−b|`. The rotator is assumed never to wrap, and
    `altair doctor` warns about this.
- **Always use the mechanical position** (NINA `ROTATOR`), not the sky position angle. The
  sky angle changes by 180° at a meridian flip even though the camera has not moved
  relative to the optics, and the optics are what the flat depends on.
- **Missing position:** if the rig has a rotator and either the light or the candidate flat
  has no position, the match **cannot be proven**. That is treated as no match
  (`ROTATOR_POSITION_UNKNOWN`). It never falls back to "probably fine".
- **Rigs without a rotator:** rotator matching is skipped. Manual camera rotation must be
  logged as an equipment event (`altair equipment log --rig X camera_rotated_manually`),
  which splits flat validity at that moment.
- **Flat masters are never averaged across rotator positions.** Flats taken at different
  positions become different masters.

### 8.3 Equipment events

A flat is valid for a light only if **no equipment event** for that rig (and for that
filter, or all filters) falls between the flat's time and the light's time. A change of
`FOCALLEN` beyond tolerance between consecutive nights automatically creates a
`reducer_changed` event proposal. It is raised as a warning issue for the user to confirm,
so the system never silently decides the optical train changed.

### 8.4 No-match behavior

The system never falls back beyond the configured tolerances. Every unmatched requirement
becomes an issue (§10) that includes the exact match key needed.

---

## 9. Multi-Night Masters

### 9.1 Principle: register once, into fixed project geometry

Each project (target, telescope, camera) has one **project reference frame**. Every night,
for every filter, is registered to it inside the night's WBPP run. As a result:

- Every night master of a project is already pixel-aligned with every other night master,
  **and across filters**, so channel combination later needs no alignment either.
- A merge is a pure pixel-wise weighted combination. **No registration is repeated.**
- A night is registered exactly once, when it is processed. The only exceptions are an
  explicit re-reference (§9.7) and reprocessing a blocked night after its calibration is
  fixed. Such a night was never registered with final calibration, so it is registered
  for the first time.

### 9.2 Choosing the project reference

- It is created by a `PROJECT_REFERENCE` job the first time a project has a night with a
  final (flat-verified) stack. The job:
  1. Calibrates that night's lights (with WBPP's registration step disabled, or reuses
     WBPP's calibrated outputs).
  2. Picks the best frame by PSF Signal Weight, from the filter with the most stars (for
     mono rigs, L or Ha is preferred, configurable per project). For OSC, it uses the
     debayered frame.
  3. Saves it as `reference_v1.xisf` and records its WCS (plate solved with
     `ImageSolver`), so overlap can be checked without star matching.
- The reference keeps the native pixel scale. Drizzle, if enabled, must use the same scale
  for the whole project (`projects.drizzle_scale`). Changing it forces a re-reference.
- Night-stack registration sets WBPP's reference to **this file**, and **autocrop is off**.
  Areas a night does not cover come out as 0 in its night master.

### 9.3 Night master geometry

Canonical night masters are stored **uncropped**, in reference geometry. Pixels a night
did not cover are exactly 0. The merge excludes them (§9.5) instead of letting them drag
the combination down. Only published *viewing* copies and the final multi-night output are
autocropped.

### 9.4 Merge eligibility gates

A night master is merged only if **all** of these hold. Otherwise
`merge_status = blocked` and an issue is raised:

| Gate | Failure issue |
|---|---|
| `kind = final` (not provisional) | — (provisional masters are never candidates) |
| `flat_verified = 1`: every light group had a flat matched per §8 (equipment, filter, focal length, rotator position, no intervening equipment event) | `FLAT_MISSING` / `ROTATOR_POSITION_UNKNOWN` |
| Dark and bias/dark-flat matched | `DARK_MISSING` / `BIAS_MISSING` / `DARKFLAT_MISSING` |
| Registered to the **current** `reference_version` | `STALE_REFERENCE` (auto-reprocess queued) |
| Same drizzle scale as the project | `SCALE_MISMATCH` |
| Overlap with the reference ≥ `min_overlap_fraction` | `LOW_OVERLAP` |
| Median FWHM ≤ `max_fwhm_ratio_to_project_median` × the project median for that filter | `QUALITY_OUTLIER` (warning; user may override with `altair night include`) |
| Not excluded by the user | — |

A blocked night does not stop the other nights from merging. The multi-night master is
rebuilt from the eligible nights, and its sidecar and the status page list every excluded
night with its reason and issue ID.

### 9.5 Merge algorithm (`mode: master_merge`, default)

A `MERGE` job for (project, filter) runs entirely in PixInsight. There is no WBPP and no
StarAlignment.

1. **Collect** all eligible night masters for (project, filter, current reference version).
2. **Normalize** (`normalization: local`): choose the normalization reference, which is the
   eligible night master with the highest measured weight. Run `LocalNormalization` for
   each night master against it, producing `.xnml` files. LN corrects night-to-night
   differences in sky background and gradient. With `global`, ImageIntegration's
   additive-with-scaling normalization is used instead.
3. **Measure the weights**, all night masters **in one pass**, so the values share one scale:
   - `measured_psf_signal` (**default**): the PSF Signal Weight of each night master,
     computed by `SubframeSelector` in measurement mode (same parameters for all masters,
     same run). PSF Signal Weight grows with the stars' signal-to-noise ratio, so it
     naturally rewards both more integration time and better nights (seeing,
     transparency, sky brightness).
   - `inverse_noise_variance`: `w = (k / σ)²`, where σ is the multiscale-median noise
     estimate of the normalized master and `k` is its photometric scale to the reference.
     This is the classical optimal weight for combining independent estimates.
   - `frame_weight_sum`: `w = Σ` raw (un-normalized) PSF Signal Weight of the frames that
     night accepted, from Altair's own per-frame measurements stored in `night.json`.
     WBPP's `WBPPWGHT` keyword is **not** used across nights, because WBPP normalizes it
     within each run, so it is not comparable between nights.
   The chosen weight is written into each master's header as `ALTNWGHT` (by a temporary
   copy in `work\`, never by modifying the canonical file).
4. **Integrate** with `ImageIntegration`:
   - `weightMode = KeywordWeight`, `weightKeyword = "ALTNWGHT"`.
   - `normalization = LocalNormalization` (or AdditiveWithScaling).
   - `rangeClipLow = true, rangeLow = 0`. This excludes each night's uncovered (zero)
     pixels, so edges are combined only from the nights that covered them.
   - `rejection = none`, because pixel rejection already happened within each night. With
     at least 8 nights, `winsorized_sigma` can be enabled to catch residual artifacts.
   - Generate the rejection maps plus a **coverage map** (the number of nights and summed
     weight per pixel). It is saved beside the master.
5. **Crop and publish:** autocrop to the region covered by at least `min_coverage_nights`
   (default: all, configurable). Publish it as a new **version**. Previous versions are
   kept up to `keep_versions`.
6. **Record** the inputs and weights (with each night's percentage contribution),
   exclusions, total exposure, and `plan_hash` in `multi_night_masters` and the sidecar.

**Rebuild vs. incremental:** the merge is always **rebuilt from all eligible night
masters** (`rebuild: from_all_nights`). This is cheap: N master-sized images, typically
under a minute. It is also order-independent, so re-including a night that was fixed later
gives the same result as if it had never been missing. Measured weights are recomputed on
every rebuild, so they always share a scale. Folding each new night into a running result
is avoided on purpose: it builds up normalization drift and can't cleanly take a night
back out.

**Why the result is close to stacking every frame at once:** within a night, WBPP already
did per-frame weighting and pixel rejection. Combining the night masters with weights that
reflect their signal-to-noise gives nearly the same signal-to-noise as a full
re-integration, at a small fraction of the cost and disk space. What is lost is
**cross-night pixel rejection** (for example a satellite trail present in a night with only
a few frames). For projects where that matters, use §9.6.

### 9.6 Optional `mode: frame_reintegration`

For maximum quality on key projects (configurable per project):

- Each night's **registered, calibrated frames** (already in reference geometry) and
  their drizzle data files are kept under `nights\<night>\<filter>\frames\`. Budget about
  100 MB per 26 MP mono frame, which is roughly 10 GB per 100-frame night.
- The merge runs `LocalNormalization` + `ImageIntegration` over **all frames from all
  eligible nights**. ImageIntegration measures weights itself (`weightMode =
  PSFSignalWeight`) in the same pass, so every frame shares one scale. Rejection uses
  full-sample statistics (for example generalized ESD). Optional `DrizzleIntegration`
  uses the kept drizzle data files.
- Registration is still never repeated. The frames were registered once, at night-stack
  time.
- Night eligibility gates (§9.4) apply unchanged. A blocked night's frames are not
  included.

### 9.7 Re-reference (explicit only)

`altair project rereference <project> [--from-night N]` picks a new reference (for
example, the first night was poor). It increments `reference_version`. All night masters
become `STALE_REFERENCE` and are reprocessed from archived raw lights. This is the only
operation that repeats registration for existing nights, and it never happens
automatically.

---

## 10. Issues, Alerts & Rerun Workflow

### 10.1 Issue kinds

| Kind | Severity | Blocks | Auto-resolves when |
|---|---|---|---|
| `FLAT_MISSING` | blocking | final night master + merge | a matching master flat is registered (built from new raw flats or imported) |
| `ROTATOR_POSITION_UNKNOWN` | blocking | final night master + merge | headers are fixed and frames re-ingested, or rig config changed |
| `DARK_MISSING` / `BIAS_MISSING` / `DARKFLAT_MISSING` | blocking | night master + merge | matching master registered |
| `HEADER_INCOMPLETE` / `UNKNOWN_RIG` / `UNKNOWN_ALIAS` | blocking (for those frames) | those frames | config or headers fixed → re-ingest |
| `LOW_OVERLAP` | blocking | merge | user includes, excludes, or re-references |
| `QUALITY_OUTLIER` | warning | merge (until acknowledged) | user includes or excludes |
| `EQUIPMENT_CHANGE_SUSPECTED` | warning | nothing (see §8.3) | user confirms or dismisses |
| `STALE_REFERENCE` | blocking | merge | reprocessing succeeds (automatic) |
| `JOB_FAILED` | blocking | that job | a successful retry or rerun |
| `DISK_SPACE_LOW` | blocking | new jobs | space freed |

Issues are **deduplicated by fingerprint**. For example, `FLAT_MISSING` for
(rig, filter, rotator bucket, night) is one issue no matter how many times the night is
re-planned. One issue can list several affected nights when they need the same flat.

### 10.2 What an alert says

Every blocking issue produces an alert with **the exact requirement** and **how to fix
it**:

```
⚠ Altair — FLAT_MISSING (issue #42) — merge blocked
Night 2026-09-24 · M31 · esprit100 / asi2600mm · filter SII
42 lights (3h30m) at rotator position 31 250 steps (±50), FOCALLEN 550 mm, bin 1x1
No master flat matches: nearest candidate is 2026-09-20 SII @ 18 400 steps (rotator Δ 12 850 > 50).

To fix, take SII flats at rotator 31 250 steps (focal length 550 mm, bin 1x1)
before any equipment change, and let NINA save them to the inbox.
Altair will build the master flat, reprocess 2026-09-24, and re-merge M31 SII automatically.
(Manual: `altair calib import <folder>` or `altair issue resolve 42 --flat <file>`.)
A provisional (no-flat) preview is at Masters\M31\2026-09-24\..._NOFLAT-PROVISIONAL.xisf
```

### 10.3 Alert channels

- **Windows toast notification**, raised when the issue opens (clicking it opens the status
  page).
- **Push and email** (Pushover / ntfy / SMTP): one summary per night, plus a separate
  message for each newly opened blocking issue. Open issues get a reminder every
  `remind_every_days`.
- **Status page** `ALTAIR_STATUS.html` (static, regenerated after every job) plus
  `ALTAIR_STATUS.json`. The page lists open issues grouped by what they need
  (a "shopping list" of flats and darks to shoot), each project's multi-night master with
  per-night weights and contributions, and excluded nights with their reasons.
- **CLI:** `altair issues [--open]`.

### 10.4 Fix → rerun loop

```
 issue opened ──► user takes flats in NINA ──► files land in inbox ──► ingest
      ▲                                                                   │
      │                                  CALIB_MASTER builds master flat ◄┘
      │                                                  │
      │               planner: does any open issue's requirement_json match?
      │                           │ yes
      │                           ▼
      │      NIGHT_STACK rerun for blocked night(s) (from archived raw lights)
      │                           │
      │                 verifier → gates (§9.4) pass?
      │               no ─────────┘      │ yes
      └── issue stays open,              ▼
          reason updated        MERGE rebuild → issue resolved (auto:<calib id>)
                                → "resolved" notification with the new multi-night master
```

- **Automatic matching:** each open issue stores a `requirement_json` (the match key and
  tolerances). Whenever a calibration master is registered, it is tested against the
  requirements of all open issues. If it matches, the dependent `NIGHT_STACK` jobs are
  re-planned (and get a new `plan_hash`, because the calibration inputs changed), followed
  by `MERGE`.
- **Raw data for reruns:** held lights are archived but stay linked to their issue. Reruns
  read them from `Archive\`. Nothing is deleted while an issue is open.
- **Manual controls:**
  - `altair issue resolve <id> --flat <path>` applies a specific master flat. The flat is
    still validated against §8, and `--force-match` is required to override a mismatch.
    Such overrides are recorded, shown on the status page and in the master's sidecar,
    and the night master is labeled `flat_override=true`.
  - `altair issue waive <id> --note "..."` permanently excludes the affected nights from
    merging and stops reminders. The night masters stay as provisional previews.
  - `altair rerun --issue <id>` / `--night <date> [--target ...] [--filter ...]` forces a
    re-plan and rerun.
  - `altair night include|exclude <project> <night> <filter>` sets a manual merge decision
    for warning-level gates.
- **NINA helper (optional):** `altair issue flats-plan [--open]` writes a CSV/Markdown
  list of (rig, filter, rotator position, binning) needing flats. A NINA Advanced
  Sequencer template generator is future work.

---

## 11. Concurrency, Robustness & Idempotency

- **One PixInsight job at a time** (default). The queue orders jobs by dependency:
  `CALIB_MASTER` → `PROJECT_REFERENCE` → `NIGHT_STACK` → `MERGE`, then by night. Merges
  for the same (project, filter) are coalesced: only the latest plan runs.
- **Crash recovery:** at startup, `running` jobs are reset to `queued`, their work
  directories are cleared, and stale PixInsight slot locks are removed. The Task
  Scheduler restart policy covers a crashed `altaird`.
- **Retries:** timeouts and crashes with no `result.json` are retried twice with backoff.
  Validation failures are not retried; they raise `JOB_FAILED`.
- **Disk guard:** a job starts only if free space ≥ 3 × input size × drizzle scale². If
  not, `DISK_SPACE_LOW` is raised.
- **Late frames:** new lights for a processed night → re-plan → a new night master
  supersedes the old one → merge rebuild.
- **Atomic publication:** outputs are written to a temporary file in the same directory
  and then renamed. The DB update and file publish use a write-ahead intent record, so a
  crash between them heals itself on restart.
- **Raw data is never deleted.** It is only moved, and only after the outputs have been
  verified.

---

## 12. Interfaces

### 12.1 CLI (`altair`)

```
altair serve [--windowless]
altair signal session-end [--source nina]
altair ingest <path>...
altair status [--night DATE] [--project P]
altair plan --night DATE                     # dry run: groups, calibration matches, gates, issues
altair run --night DATE [--force] [--only target=M31,filter=Ha]
altair rerun --issue ID | --night DATE [...]
altair issues [--open] | issue show|resolve|waive|flats-plan ...
altair calib list | import <dir> | build --night DATE
altair equipment log --rig R <kind> [--filter F] [--at TIME] [--note ...] | list
altair project list | show P | rereference P [--from-night N] | set-mode P master_merge|frame_reintegration
altair night include|exclude <project> <night> <filter>
altair merge <project> [--filter F] [--dry-run]   # prints nights, weights, contributions
altair doctor                                     # PixInsight/CLI flags, session type, paths, disk, NINA headers sample
```

### 12.2 Optional HTTP status endpoint

A read-only FastAPI endpoint on `127.0.0.1` (`/status`, `/issues`, `/projects/<id>`), for
Home Assistant or dashboards.

---

## 13. Observability

- Structured JSON logs (by night, project, job, and issue).
- A PixInsight console log per job, kept for 90 days.
- A per-night report (`Masters\<target>\<night>\report.md`): frames used and rejected,
  per-frame weights, FWHM and eccentricity, calibration used with match evidence
  (including rotator Δ), and timings.
- A per-merge report: nights, weights, % contribution, excluded nights, coverage map
  preview.
- Optional Prometheus-format metrics.

---

## 14. Future Work

- NINA Advanced Sequencer template generation for missing flats.
- A NINA plugin that posts session-end and target events directly (instead of an External
  Script).
- Cross-rig projects (combining data from different telescopes or cameras, which needs
  resampling between different geometries).
- Pre-screening frames before WBPP using NINA's HFR and star-count metadata.
- Automatic hand-off to a post-processing stage.

---

## 15. Implementation Plan & Milestones

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **0: Windows/PixInsight spike** | Headless PixInsight on Windows from a Task-Scheduler-launched process. A PJSR runner that (a) drives WBPP's engine with a **manual registration reference** and autocrop off, and (b) runs `SubframeSelector` measurement + `LocalNormalization` + `ImageIntegration` with `KeywordWeight` and `rangeClipLow`. Confirm the NINA header keywords, including `ROTATOR` units, on your own files. | A single command produces a night master in reference geometry and a 2-night merge with keyword weights. Documented in `docs/pixinsight-cli.md`. |
| **1: Catalog & NINA ingest** | Config, header mapping, rig and rotator extraction (header or file name, degrees or steps), exclusive-open stability check, SQLite schema. | A real NINA night indexes correctly. Tests cover rotator parsing and wrap-around. |
| **2: Planner & matching** | §8 rules, including rotator and equipment events, issue generation, `altair plan`. | Table-driven tests for every rule: rotator Δ at tolerance ±1 step, wrap-around, missing position, event between flat and light. |
| **3: Night stacks** | Executor (Job Objects, timeouts), `PROJECT_REFERENCE`, `NIGHT_STACK`, verifier, publisher, archive. | A night master matches a manual WBPP run on the same data within noise, and is pixel-aligned with the reference. |
| **4: Calibration library** | `CALIB_MASTER`, rotator-tagged master flats, import, supersession. | A flats-only night produces masters that are matched automatically. |
| **5: Merger** | §9: gates, measured weighting, LN, coverage-aware integration, versions, reports. | A 3-night merge's weights match the measured PSF signal ordering. Excluding a night and re-including it reproduces the same result byte for byte. Uncovered edges don't darken the result. |
| **6: Issues & rerun loop** | §10: issue store, dedup, toast, push, email, status page, auto-rerun on calibration arrival, manual resolve and waive. | End to end: a night without SII flats → issue + toast → drop matching flats into the inbox → automatic rerun → merge → "resolved" notification, with no CLI use. |
| **7: Daemon & hardening** | Triggers, NINA sentinel, Task Scheduler install script, sleep prevention, crash recovery, `altair doctor`. | A week of unattended real nights. Survives killing `altaird` and `PixInsight.exe` mid-job. |

### 15.1 Proposed source layout

```
altair-pre-processor/
├── pyproject.toml
├── altair.example.yaml
├── src/altair/
│   ├── cli.py  config.py  daemon.py
│   ├── triggers/     # sentinel.py, watcher.py, schedule.py, dawn.py
│   ├── ingest/       # headers.py, nina.py, rotator.py, normalize.py, classify.py
│   ├── catalog/      # db.py, models.py, migrations/
│   ├── planner/      # grouping.py, matching.py, gates.py, plan.py
│   ├── executor/     # pixinsight.py, winjob.py (Job Objects), queue.py
│   ├── projects/     # reference.py, merge.py, weights.py
│   ├── issues/       # model.py, requirements.py, resolver.py, status_page.py
│   ├── publish/      # verify.py, stamp.py, archive.py, report.py
│   └── notify/       # toast.py, pushover.py, ntfy.py, email.py
├── pjsr/
│   ├── altair_runner.js          # dispatch on job.kind
│   ├── wbpp_driver.js            # headless WBPP engine driver
│   ├── native_pipeline.js        # fallback
│   ├── merge.js                  # SSF measure + LN + ImageIntegration(KeywordWeight)
│   └── lib/json_io.js
├── deploy/windows/
│   ├── install-task.ps1          # registers the Task Scheduler task
│   └── nina-external-script.md
├── tests/
│   ├── fixtures/                 # synthetic NINA-style FITS (astropy), tiny images
│   ├── test_rotator.py  test_matching.py  test_gates.py  test_issues.py
│   ├── test_weights.py           # weighting math on synthetic noise/signal
│   └── test_executor_contract.py # fake PixInsight.exe writing result.json
└── docs/
    ├── SPEC.md
    └── pixinsight-cli.md
```

### 15.2 Testing strategy

- **Unit:** normalization, rotator parsing and circular distance, flat matching across
  equipment events, merge gates, issue dedup and requirement matching, session rollover
  (including DST changes).
- **Weights:** synthetic masters with known noise σ and scale. Checks that
  `inverse_noise_variance` recovers the optimal weights, and that the ordering of all
  weighting modes is consistent.
- **Contract:** a fake `PixInsight.exe` (a small Python-built exe or `.cmd` shim) that
  validates `job.json` and emits canned outputs. This runs in CI on `windows-latest`.
- **Integration (local, marked):** real PixInsight on a small reference dataset of 2
  nights, 2 filters, and one rotator change, with one night deliberately missing flats.
  Golden checks are statistical (median, MAD, star count, weight ordering), not
  byte-for-byte.
- **Soak:** replay a recorded NINA night's file arrivals into the inbox.

---

## 16. Risks & Open Questions

| # | Item | Mitigation / decision |
|---|---|---|
| R1 | WBPP is not an official headless API, and its engine internals change between releases. | Pin the tested versions. `altair doctor` checks them. Keep the native-pipeline fallback behind the same contract. |
| R2 | PixInsight needs an interactive Windows session. | Task Scheduler at logon plus auto-logon. `doctor` detects session 0. |
| R3 | WBPP may not respect a manual reference file combined with autocrop off in every version. | Phase 0 verifies it. If not, the native pipeline's `StarAlignment` does registration to the reference. |
| R4 | The rotator position keyword or units vary by rotator driver. | Configurable per rig (keyword or file-name regex, degrees or steps, wrap). Missing value = no match, never a guess. |
| R5 | Weights must share a scale across nights. | Weights are re-measured together at every merge (§9.5). WBPP's per-run normalized weights are never used across nights. |
| R6 | Disk usage in `frame_reintegration` mode. | Off by default, enabled per project. Disk guard. |
| R7 | Processing on the NINA PC could interfere with a new session. | Gated on session end, below-normal priority, no new jobs while NINA is writing. |
| Q1 | Is the rotator reported in degrees or steps, and is steps-per-revolution known? Which rotator and driver? | Sets the rig's `rotator` defaults. |
| Q2 | Same PC for NINA and processing, or a separate processing PC reading a share? | Changes the inbox path, the file-event reliability plan, and the §4.1 gating. |
| Q3 | Should the multi-night master require every night to cover the full frame (strict crop), or allow partial-coverage edges? | Default: crop to full coverage (`min_coverage_nights: all`). |
| Q4 | Default weighting: `measured_psf_signal` or `inverse_noise_variance`? | Spec default is PSF signal (it rewards seeing and transparency too). Phase 5 compares both on real data. |
