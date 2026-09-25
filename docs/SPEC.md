# Altair Pre-Processor — Implementation Specification

**Status:** Draft v0.3
**Date:** 2026-09-25
**Target platform:** Windows 10/11 (x64). Each telescope has its own NINA mini PC, and a
separate, more powerful processing PC runs PixInsight 1.9.x with WBPP 2.x. They share a
network folder, with S3 backup and an optional large NFS archive.

### Changelog

| Version | Changes |
|---|---|
| v0.3 | Distributed storage. A small agent on each rig PC delivers files to the shared folder with SHA-256 manifests. The catalog tracks files by content hash across several storage locations (shared folder, NFS, S3, local cache), so it doesn't depend on where a file currently lives. A staging layer fetches job inputs from whichever location has them, including S3 Glacier restores. Files are deleted from the shared folder only after a verified durable copy exists. Catalog backup and a rebuild-from-archive path for disaster recovery. Raw files are no longer moved. (§4, §6, §7, §10, §11) |
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
- **Works wherever the data lives:** jobs ask for files by content hash, not by path. Any
  file the pipeline needs, including raw lights from months ago for a rerun or
  re-reference, is fetched automatically from whichever storage location still has it
  (shared folder, NFS, S3 including Glacier, or another file system), and checked against
  its hash before use. Deleting local copies is safe because nothing is deleted unless a
  verified durable copy exists.
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
| **Rig PC** | The NINA mini PC attached to one telescope. It runs `altair agent`. |
| **Processing PC** | The machine that runs `altaird` and PixInsight. |
| **Blob** | One file's content, identified by its SHA-256 hash. The catalog refers to data only by blob hash plus a *logical path*, never by a physical path. |
| **Location** | A configured place blobs can live: `landing` (the shared folder), `nfs`, `s3`, `cache` (processing PC SSD), or `rig:<name>` (a rig PC's local disk). |
| **Replica** | One copy of a blob in one location. It is tracked with state (`present`, `missing`, `archived_cold`, `restoring`) and when it was last verified. |
| **Durable location** | A location whose replicas count as backup (S3, and NFS if configured that way). The shared folder and cache are **not** durable. |
| **Staging** | Making sure every input blob of a job is in the local cache, verified, before PixInsight starts. |

---

## 3. High-Level Architecture

### 3.0 Physical topology

```
 ┌── Rig PC A (mini PC) ──┐   ┌── Rig PC B (mini PC) ──┐
 │ NINA → D:\NINA (local)  │   │ NINA → D:\NINA (local)  │
 │ altair agent (service)  │   │ altair agent (service)  │
 │  copy+hash → manifest   │   │  copy+hash → manifest   │
 └───────────┬─────────────┘   └───────────┬─────────────┘
             │ SMB                         │ SMB
             ▼                             ▼
      ┌──────────────────────────────────────────────┐        ┌───────────────────┐
      │ Shared folder  \\nas\astro  (location:landing)│───────►│ S3 bucket (durable)│
      │ not durable, space may be reclaimed           │ upload │ STANDARD / IA /    │
      └──────────────────────┬───────────────────────┘        │ GLACIER tiers      │
                             │                                 └─────────┬─────────┘
       optional ┌────────────┴─────────┐                                  │
       NFS ────►│ NFS archive (durable) │                                  │
                └────────────┬─────────┘                                  │
                             ▼            fetch from the best location     │
      ┌──────────────────────────────────────────────┐◄───────────────────┘
      │ Processing PC: altaird + PixInsight            │
      │ local SSD cache (location:cache) ← staging     │
      │ catalog DB (backed up to S3 nightly)           │
      └──────────────────────────────────────────────┘
```

- **Rig PCs** only capture and deliver. NINA always saves to the **local disk** first, so a
  network or NAS outage never costs frames. The agent copies files to the shared folder
  and verifies each copy.
- The **processing PC** never reads a network path from PixInsight. Every job input is
  staged into its local SSD cache first (§7.5).
- **S3** (and NFS, if you add it) is the durable archive. The shared folder is a landing
  zone and can be emptied under the rules in §7.6.

### 3.1 Software components

```
           ┌───────────────────────────────────────────────────────────────────────┐
           │                     altaird (Processing PC, Windows)                   │
 agents ─► │ 1 Trigger ─► 2 Ingest & ─► 3 Planner ─► 4 Stager ─► 5 Executor ─►      │
 (manifest │   Detector     Catalog      (group,      (fetch     (PixInsight       │
 + session │   (manifests,  (headers,    calib/flat   inputs to   WBPP, per        │
 end)      │   schedule)    blobs,       matching)    cache)      night)           │
           │                replicas)        │            ▲            │           │
           │                                 │            │            ▼           │
           │   Calibration Library ◄─────────┤   Storage Manager   6 Verifier &     │
           │                                 │   (locations,       Publisher        │
           │                                 │    replication,         │            │
           │                                 │    S3 restore,          ▼            │
           │                                 │    reclaim)        7 Merger         │
           │                                 ▼                        │            │
           │                          8 Issue Tracker ◄──── gates ────┘            │
           │                                 │   ▲                                 │
           │                                 │   └── new calibration → auto-rerun  │
           │                                 ▼                                     │
           │                          9 Notifier (toast, push, email, status page) │
           └───────────────────────────────────────────────────────────────────────┘
```

The **orchestrator** (`altaird`) is a long-running Python service on the processing PC.
PixInsight is an external worker that the orchestrator starts once per job, as a separate
process. The **agent** (`altair agent`) is a small service from the same Python package,
running on each rig PC.

### 3.2 Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Content identity | SHA-256, computed **once, on the rig PC**, as each file is delivered | A hash that stays the same across every location. It catches corruption in any copy, download, or restore. |
| Object storage | `boto3` against any S3-compatible endpoint (AWS, Backblaze B2, Wasabi, MinIO) | One interface for AWS and self-hosted storage. Storage-class and restore aware. |
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

**Rig PCs (`altair agent`).** The agent needs no desktop, so it runs as a normal
**Windows service** (installed with `altair agent install`). It runs under a service
account that has write access to the shared folder, with the share credentials stored in
Windows Credential Manager. It uses little CPU and runs at `BELOW_NORMAL` priority, so it
never competes with NINA for USB or disk bandwidth during capture. If
`agent.defer_copy_while_capturing: true`, it delays copying until the session ends.

**Processing PC (`altaird`).** PixInsight is a Qt GUI application. Even in `--automation-mode` it needs an **interactive
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

**Separate processing PC:** capture and processing are on different machines, so
processing never competes with acquisition. A single-PC setup (NINA and `altaird`
together) is still supported. In that setup the agent's target is a local folder, and
processing only starts after the session ends, at `BELOW_NORMAL` priority.

**Keeping the PC awake:** while any job is queued or running, `altaird` calls
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`. `altair doctor` warns if
Windows Update active hours overlap the configured processing window.

**Process control:** each PixInsight run is assigned to a **Windows Job Object** with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. On timeout or crash, the whole process tree is killed
reliably and no orphaned `PixInsight.exe` is left holding the instance slot.

**Performance notes (checked by `altair doctor`):** PixInsight swap directories should be
on a fast SSD. The `cache`, `work`, and `projects` directories should be excluded from
Windows Defender real-time scanning. Long-path support should be enabled
(`LongPathsEnabled=1`), because WBPP output paths get deep. A 2.5 GbE (or faster) link
between the processing PC and the NAS is recommended. At 1 GbE, staging a 100-frame
night (about 5 GB of 16-bit frames) takes about a minute.

### 4.2 NINA integration

**Where NINA saves.** NINA saves to a folder on the rig PC's **local disk** (for example
`D:\NINA`), never directly to the share. The agent watches that folder.

**Session-end signal.** In the NINA Advanced Sequencer, add an **External Script**
instruction to the sequence's *End* area (and optionally after each target's flats). It
runs on the **rig PC**:

```
"C:\Program Files\Altair\altair.exe" agent signal session-end
```

This tells the local agent that the night is over. The agent finishes delivering every
file, then publishes the night's **delivery manifest** (§7.3) to the shared folder. The
manifest's arrival is what makes the night ready for processing. If the sequence aborts or
NINA crashes, the agent's own quiescence timer (no new files for `quiescence_minutes`,
after dawn) publishes the manifest instead, and the processing PC's scheduled fallback
(§6.1) is the last safety net.

**Recommended NINA image file pattern** (Options → Imaging). It is not required, because
Altair relies on headers, but it keeps folders readable. The agent keeps this relative
path under the rig's folder in the shared folder:

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
The processing PC uses the whole file. Each rig PC's agent reads only `site`, `agent`,
and its own entry under `rigs`. The file can be shared, or the agent can get a small
generated `agent.yaml` from `altair agent config export --rig <name>`.

```yaml
site:
  name: "Backyard Observatory"
  latitude: 37.3
  longitude: -121.9
  timezone: "America/Los_Angeles"
  session_rollover_local: "12:00"

paths:                                    # processing PC local paths only
  work: "E:/AltairWork"                   # per-job scratch space (fast SSD)
  state: "C:/ProgramData/Altair/state"    # catalog DB, logs
  published: "//nas/astro/Masters"        # user-facing copies of masters (convenience, not the record)

storage:                                  # see §7
  cache:
    path: "E:/AltairCache"                # location "cache": processing PC SSD
    max_size_gb: 800
    min_free_gb: 100
    pin: [calibration_master, project_reference, night_master, multi_night_master]
  locations:
    - name: landing
      kind: fs
      root: "//nas/astro/Landing"         # agents deliver here
      role: landing
      durable: false
      reclaimable: true                   # may be emptied; see storage.reclaim
      read_priority: 10                   # lower = tried first (after cache)
    - name: nfs
      kind: fs
      root: "//bignfs/astro-archive"      # a UNC path works for SMB or the Windows NFS client
      durable: true
      reclaimable: false
      read_priority: 20
      enabled: false                      # turn on if/when the NFS is added
      replicate: [raw, calibration_raw, calibration_master, night_master, multi_night_master, catalog_backup]
    - name: s3
      kind: s3
      bucket: "my-astro-archive"
      prefix: "altair/"
      region: "us-west-2"
      endpoint_url: null                  # set for B2 / Wasabi / MinIO
      credentials: { profile: "altair" }  # AWS profile, or Windows Credential Manager target
      durable: true
      read_priority: 30
      managed_by: altair                  # altair (Altair uploads; recommended) | external (see §7.4)
      replicate: [raw, calibration_raw, calibration_master, project_reference, night_master, multi_night_master, catalog_backup]
      storage_class:                      # per data class; applied on upload
        raw: STANDARD_IA                  # or GLACIER_IR / DEEP_ARCHIVE — see §7.5 restore rules
        calibration_raw: DEEP_ARCHIVE
        default: STANDARD               # bucket lifecycle transitions are detected at read time (HeadObject)
      restore:
        tier: Bulk                        # Bulk | Standard | Expedited
        days: 7
        max_auto_restore_gb: 50           # above this, a RESTORE_APPROVAL_NEEDED issue is raised
      max_auto_download_gb: 200          # above this, approval is required (egress cost guard)
      bandwidth_limit_mbps: null
      multipart_chunk_mb: 64
  reclaim:
    landing:
      enabled: true
      min_age_days: 14                    # never before this
      require_durable_copies: 1           # verified replicas in durable locations
      require_processed: true             # the night's stacks finished (or were waived)
      keep_if_open_issue: true            # frames tied to an open issue stay on landing
      target_free_percent: 25             # reclaim oldest-first until this much is free
  verify:
    s3_after_upload: checksum             # checksum (SHA-256 stored + S3 checksum) | head_only
    scrub_interval_days: 90               # re-verify a random sample of replicas
    scrub_sample_percent: 2

agent:                                    # rig PC agent settings
  landing_root: "//nas/astro/Landing"
  local_retention_days: 7                 # delete from the rig PC only after verified on landing (and durable if required)
  local_delete_requires_durable: false    # true = also wait for the S3 copy
  quiescence_minutes: 45
  defer_copy_while_capturing: false
  heartbeat_interval_s: 60

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
  require_after_dawn: true
  scheduled_fallback_local: "09:00"
  rescan_interval_minutes: 15
  manifest_grace_minutes: 30              # wait for straggler files listed in a manifest
  min_lights_per_stack: 5

rigs:
  esprit100_2600mm:
    host: "rig-esprit"                    # the rig PC's name; its agent authenticates with this
    nina_save_root: "D:/NINA"             # on the rig PC
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
    host: "rig-rasa"
    nina_save_root: "D:/NINA"
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

Nights are processed **per rig**. Each rig PC finishes its night on its own schedule.

1. **Delivery manifest (primary).** A rig's agent publishes
   `Landing\<rig>\_manifests\<night>.json` (§7.3) after NINA's session-end script, or
   after the agent's own quiescence timer. The night is **ready** once every file listed
   in the manifest is present on landing with a matching size and SHA-256. Files still
   missing after `manifest_grace_minutes` raise `DELIVERY_INCOMPLETE`. Readiness is
   decided by the manifest's list of files, not by guessing from timing.
2. **Scheduled fallback.** A daily run at `scheduled_fallback_local`, plus a periodic
   rescan of landing. This covers a missing manifest (for example, a rig PC crashed
   before publishing one). Files found without a manifest are hashed on the processing
   PC, and the night is processed with a warning (`MANIFEST_MISSING`).
3. **Calibration arrival.** Newly indexed calibration frames or masters that could resolve
   an open issue (§10) trigger a targeted re-plan.
4. **Data availability.** A blob that was unavailable becomes reachable again (a location
   comes back online, or an S3 restore completes). Jobs waiting on it re-queue (§7.5).
5. **Manual.** `altair run …` / `altair rerun …`.

**File stability** is the agent's job (§7.2). The processing PC only ingests files that a
manifest lists, or that it has hashed itself.

### 6.2 Ingest & Catalog

For each delivered file (listed in a manifest, or found by a rescan):

1. Register the **blob** (SHA-256 from the manifest), its **logical path**
   (`raw/<rig>/<relative NINA path>`), and a `present` **replica** in `landing`. If the
   blob is already known (a duplicate delivery or a re-sync), it is linked to the existing
   blob and never processed twice.
2. Read the headers and resolve canonical fields through `header_mapping` and `aliases`.
3. Resolve the **rig** from (telescope, camera). This is cross-checked against the rig
   whose agent delivered the file. Frames that match no configured rig get
   an `UNKNOWN_RIG` issue.
4. Extract the **rotator position** using the rig's rotator config: read the keyword or
   file-name token, parse it as a number in `units`, and store it as `rotator_pos`
   together with `rotator_units`.
5. Classify the image type from NINA's `IMAGETYP` values.
6. Compute `night` (noon-to-noon local time).
7. Store the row in `frames`. Frames missing required fields are `invalid` and get an issue
   (`HEADER_INCOMPLETE`). Their blobs are kept, so you can add aliases or fix the config,
   and `altair rerun` picks them up. Headers are cached in the catalog, so re-planning never
   has to fetch a file.
8. Queue replication of the blob to every durable location that its data class lists
   (§7.4).

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

All files are referenced by **blob hash** (SHA-256). Physical paths live only in `replicas`.
So moving, reclaiming, or restoring data never changes the catalog's view of *what* the
data is. It only changes *where* the data can be fetched from.

```sql
-- ── Storage (§7) ──────────────────────────────────────────────────────────
CREATE TABLE blobs (
  sha256 TEXT PRIMARY KEY,
  size_bytes INTEGER NOT NULL,
  data_class TEXT NOT NULL,      -- raw / calibration_raw / calibration_master / project_reference /
                                 -- night_master / multi_night_master / registered_frame / catalog_backup / report
  logical_path TEXT NOT NULL,    -- e.g. raw/esprit100_2600mm/2026-09-24/M31/LIGHT/Ha/..._0001.fits
  origin_rig TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(logical_path, sha256)
);

CREATE TABLE locations (
  name TEXT PRIMARY KEY,         -- landing / nfs / s3 / cache / rig:<name>
  kind TEXT NOT NULL,            -- fs / s3
  durable INTEGER NOT NULL,
  reachable INTEGER NOT NULL,    -- last probe result
  last_probe_at TEXT
);

CREATE TABLE replicas (
  sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  location TEXT NOT NULL REFERENCES locations(name),
  uri TEXT NOT NULL,             -- physical path or s3://bucket/key
  state TEXT NOT NULL,           -- present / missing / archived_cold / restoring / restored / corrupt
  storage_class TEXT,            -- S3 only (STANDARD, STANDARD_IA, GLACIER, DEEP_ARCHIVE, ...)
  restore_expires_at TEXT,       -- S3 only
  verified_at TEXT,              -- last time the content was verified against sha256
  verify_method TEXT,            -- sha256_full / s3_checksum_sha256 / size_only
  last_seen_at TEXT,
  PRIMARY KEY (sha256, location)
);

CREATE TABLE fetch_requests (    -- staging and restore bookkeeping (§7.5)
  id INTEGER PRIMARY KEY,
  job_id INTEGER REFERENCES jobs(id),
  sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  source_location TEXT,
  state TEXT NOT NULL,           -- pending / restoring / downloading / done / failed / awaiting_approval
  bytes_done INTEGER DEFAULT 0,
  error TEXT, updated_at TEXT
);

CREATE TABLE deliveries (        -- one row per agent manifest
  rig TEXT NOT NULL, night TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
  n_files INTEGER, total_bytes INTEGER, complete INTEGER NOT NULL,
  received_at TEXT, PRIMARY KEY (rig, night, manifest_sha256)
);

-- ── Science catalog ───────────────────────────────────────────────────────
CREATE TABLE frames (
  id INTEGER PRIMARY KEY,
  sha256 TEXT UNIQUE NOT NULL REFERENCES blobs(sha256),
  image_type TEXT NOT NULL, night TEXT NOT NULL, date_obs TEXT NOT NULL,
  rig TEXT, telescope TEXT, camera TEXT, filter TEXT, target TEXT,
  focal_length REAL, exposure REAL, gain INTEGER, offset INTEGER, sensor_temp REAL,
  binning TEXT, readout_mode TEXT, width INTEGER, height INTEGER, bayer_pattern TEXT,
  rotator_pos REAL, rotator_units TEXT,
  raw_headers_json TEXT,
  status TEXT NOT NULL,          -- new/valid/invalid/held/processed/rejected
  status_reason TEXT
);

CREATE TABLE calibration_masters (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,            -- DARK/FLAT/BIAS/DARKFLAT
  sha256 TEXT UNIQUE NOT NULL REFERENCES blobs(sha256),
  source_frames_json TEXT,       -- sha256 of every raw sub it was built from
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
  reference_sha256 TEXT REFERENCES blobs(sha256),
  reference_night TEXT, reference_version INTEGER NOT NULL DEFAULT 1,
  pixel_scale_arcsec REAL, drizzle_scale INTEGER NOT NULL DEFAULT 1,
  UNIQUE(target, telescope, camera)
);

CREATE TABLE night_masters (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  night TEXT NOT NULL, filter TEXT NOT NULL,
  sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  input_frames_json TEXT NOT NULL,  -- sha256 of every light used (so reruns know what to fetch)
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
  UNIQUE(project_id, night, filter, kind, reference_version, sha256)
);

CREATE TABLE multi_night_masters (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  filter TEXT NOT NULL, version INTEGER NOT NULL,
  sha256 TEXT NOT NULL REFERENCES blobs(sha256),
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
  status TEXT NOT NULL,           -- queued/staging/waiting_data/running/succeeded/failed/skipped/blocked
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
     `held`: they are replicated to durable storage like any other raw file, but stay
     linked to the open issue so the rerun knows exactly which blobs to fetch.
   - Missing dark or bias/dark-flat → blocked. No provisional master. The issue is raised.
5. **Project reference.** If the project has no reference yet, a `PROJECT_REFERENCE` job
   runs before the first `NIGHT_STACK` (§9.2).
6. **Merge.** After a night's stacks finish, a `MERGE` job is emitted for each affected
   (project, filter).
7. **Idempotency.** `plan_hash` is the SHA-256 of the canonical plan JSON: input hashes,
   calibration hashes, resolved settings, reference version, and software versions. A plan
   whose `plan_hash` already succeeded is skipped.
8. **Data cost estimate.** For each job, the planner records the total input size and
   where each input would come from (cache, landing, NFS, S3 hot, S3 cold). That feeds the
   approval guards and ETAs in §7.5. Planning itself never needs file contents, because
   headers are cached in the catalog.

### 6.5 Executor (PixInsight on Windows)

**Before PixInsight starts, every job is staged** (§7.5). All input blobs are made
present and verified in the local cache, and then hard-linked into
`work\<job-id>\inputs\` under their original file names. A job whose inputs are not all
available goes to `waiting_data` instead of `running`. So a slow S3 restore never ties up
the PixInsight slot, and other ready jobs run in the meantime.

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
2. **Register outputs as blobs:** the canonical copy (uncropped, project geometry) is
   stored under the logical path `projects/<project>/nights/<night>/<filter>/`, together
   with `night.json`, which holds the calibration evidence, metrics, per-frame weights,
   rejections, and the SHA-256 of every input. Both go into the cache (pinned) and are
   queued for replication to durable locations (§7.4).
3. **Publish a viewing copy** (autocropped) to
   `published\<target>\<night>\<target>_<telescope>_<camera>_<filter>_<night>_<N>x<exp>s.xisf`.
   Provisional masters get the suffix `_NOFLAT-PROVISIONAL`. Published copies are for
   convenience only. They are not tracked as replicas and can be regenerated at any time
   (`altair publish --refresh`).
4. **Raw lights are not moved.** "Archiving" means verified replication to durable
   locations (§7.4). Raw files stay where they were delivered until landing reclaim
   (§7.6) removes them.
5. **Record the night master** with `merge_status`, set by the gates in §9.4.

### 6.7 Merger

See §9.

### 6.8 Issue Tracker & Notifier

See §10.

---

## 7. Storage, Archive & Data Retrieval

### 7.1 Principles

1. **Content-addressed.** Every file is a blob identified by SHA-256. The hash is computed
   **on the rig PC, before the file crosses the network**, so every later copy, upload,
   download, and restore can be checked against it.
2. **The catalog doesn't depend on where files are.** The catalog says *what* data exists.
   `replicas` say *where* it currently is. Any location may lose its copy at any time
   without affecting correctness. The only effect is where the next fetch comes from.
3. **Raw files are never changed or moved.** "Archiving" means replicating to durable
   locations, not moving. Moving files would break external backup tools and make the
   recorded replicas wrong.
4. **Nothing is deleted without a verified durable copy.** This is a hard rule with no
   override: Altair never deletes the only verified copy of a blob.
5. **PixInsight only reads the local cache.** Network paths are never handed to PixInsight.
6. **Every input can be re-fetched.** A job needs only blob hashes. The staging layer finds
   a copy, restores it if it is in cold storage, downloads it, verifies it, and hands it to
   the job.

### 7.2 Agent delivery protocol (rig PC → landing)

For each new file in the NINA save folder:

1. **Stability:** its size and mtime have not changed for 30 s, it opens with **exclusive
   share mode** (so NINA has finished writing it), and it parses as FITS or XISF.
2. **Hash locally:** a streaming SHA-256 of the file. The agent also parses the FITS header
   into a small dict.
3. **Copy safely:** write to `Landing\<rig>\<relative path>.partial`, flush, then
   **read back and re-hash** from the share (`verify: readback`, the default; `size`
   skips the read-back). Then rename to the final name. A mismatch triggers a retry, and
   after 3 failures raises `DELIVERY_CORRUPT`.
4. **Journal:** record (file, sha256, size, delivered_at) in the agent's local SQLite
   journal. Delivery is idempotent: a file already delivered with the same hash is skipped.
5. **Share offline:** the agent queues and retries with backoff. NINA keeps saving
   locally, so size rig PC disks for at least `local_retention_days` plus a few nights of
   backlog. The agent writes a heartbeat to `Landing\_agents\<rig>.json` every
   `heartbeat_interval_s`, with its backlog size. The processing PC raises
   `RIG_AGENT_OFFLINE` (stale heartbeat) or `RIG_DELIVERY_BACKLOG` (backlog > 12 h).
6. **Manifest:** at session end (the NINA script or quiescence), after every file of the
   night has been delivered, the agent writes `Landing\<rig>\_manifests\<night>.json`
   atomically (temp file, then rename):

```json
{
  "schema": 1,
  "rig": "esprit100_2600mm",
  "night": "2026-09-24",
  "closed_by": "nina_session_end",
  "agent_version": "0.3.0",
  "files": [
    {
      "logical_path": "raw/esprit100_2600mm/2026-09-24/M31/LIGHT/Ha/2026-09-24_23-10-02_Ha_300.00s_0001.fits",
      "sha256": "9f2c…",
      "size": 52436160,
      "headers": { "IMAGETYP": "LIGHT", "FILTER": "Ha", "ROTATOR": 31250, "…": "…" }
    }
  ]
}
```

   Because the manifest carries the headers, the catalog can be rebuilt from manifests
   alone without downloading any image (§7.8).
7. **Local cleanup on the rig PC:** a local file is deleted only when (a) its landing copy
   is verified, (b) it is older than `local_retention_days`, and (c) if
   `local_delete_requires_durable`, the processing PC has published
   `Landing\_acks\<rig>\<night>.json`, meaning every blob in that manifest has a verified
   durable replica.
8. **Re-delivery (last resort):** the rig PC is a readable location (`rig:<name>`) while it
   still holds a file. If a blob is unavailable everywhere else, the processing PC writes
   a request to `Landing\_requests\<rig>.json`, and the agent re-delivers any requested
   files it still has.

### 7.3 Logical layout and data classes

Every blob has a **logical path**. In file-system locations the physical path is
`<root>\<logical path>`. With `managed_by: altair`, the S3 key is `<prefix><logical path>`.
Using the same tree everywhere keeps every location browsable by hand and makes
rebuild-from-archive possible.

| Data class | Logical path | Durable replication (default) | Cache | Regenerable? |
|---|---|---|---|---|
| `raw` (lights, flats, darks, bias, dark-flats as delivered) | `raw/<rig>/<night>/<NINA relative path>` | S3 (+ NFS) — **required** | on demand | **No** |
| `calibration_master` | `calibration/masters/<kind>/<camera or telescope/camera/filter>/…xisf` (+ `.json`) | S3 (+ NFS) | pinned | Yes, from raw calibration subs |
| `project_reference` | `projects/<project>/reference/reference_v<N>.xisf` | S3 (+ NFS) | pinned | **No** — every night master depends on it |
| `night_master` (+ `night.json`) | `projects/<project>/nights/<night>/<filter>/…` | S3 (+ NFS) | pinned | Yes, from raw (costly) |
| `multi_night_master` (+ sidecar) | `projects/<project>/multinight/<filter>/v<NNN>.xisf` | S3 (+ NFS) | latest pinned | Yes, from night masters (cheap) |
| `registered_frame` (frame_reintegration mode only, §9.6) | `projects/<project>/nights/<night>/<filter>/frames/…` | optional | on demand | Yes, from raw + reference |
| `manifest` | `raw/<rig>/_manifests/<night>.json` | S3 (+ NFS) | — | No |
| `catalog_backup` | `catalog/altair-<utc>.db.zst` | S3 (+ NFS) | — | Partly, see §7.8 |

Pinned classes are small (masters are one image each), so the processing PC keeps them
all locally. Merges therefore never wait on the network.

### 7.4 Replication to durable locations

A **replicator** worker in `altaird` goes through `(blob, target location)` pairs. Raw
files come first (they can't be replaced), then masters, then everything else.

- **S3 upload (`managed_by: altair`, recommended):** a multipart upload with
  `ChecksumAlgorithm=SHA256`, so S3 checks every part on arrival. The full-file SHA-256 is
  also stored as object metadata `x-amz-meta-altair-sha256`, because multipart checksums
  are composite. After upload, `HeadObject` (with `ChecksumMode=ENABLED`) confirms the
  object and its metadata. Only then is the replica marked `present`, verified
  `s3_checksum_sha256`.
- The storage class is set per data class (`storage_class`). Bucket lifecycle rules that
  move objects later (for example to Glacier after 90 days) are fine: the storage class
  is re-read with `HeadObject` whenever a fetch needs it.
- **File-system locations (NFS or another share):** copy to a temp file, read it back and
  re-hash it, then rename.
- **Upload window and bandwidth:** optional `upload_window: "09:00-18:00"` and
  `bandwidth_limit_mbps`, so uploads don't saturate the uplink overnight.
- **Recommended bucket settings:** versioning **on** (a deleted or overwritten object can
  still be recovered), an optional Object Lock or retention policy for `raw/`, and
  server-side encryption. `altair doctor` reports these.
- **`managed_by: external`** (you keep an existing backup tool that copies the share to
  S3):
  - Altair uploads nothing to that location. It **discovers** replicas instead: a
    `key_template` (for example `"nas-backup/astro/Landing/{landing_relpath}"`) maps each
    landing file to its expected key, and a periodic scan (`ListObjectsV2`, or an S3
    Inventory report for large buckets) marks matching objects `present` with
    `verify_method = size_only`.
  - A `size_only` replica is verified by hash the first time it is actually fetched.
  - **Reclaim risk:** many sync tools **mirror deletions**. If yours does, reclaiming
    landing would delete the backup as well. So with an external backup, landing reclaim
    is refused unless the config states
    `external_backup_retains_deleted_files: true` (a versioned bucket, or a copy-only
    job), *and* `trust_size_match_for_reclaim: true` is set. Otherwise every blob must be
    hash-verified by download first, which costs egress.
  - Because of this risk, `managed_by: altair` is the recommended setup.

`DATA_AT_RISK` (blocking) is raised when a `raw` blob older than 48 h still has no
verified durable replica (for example, uploads keep failing). A daily summary shows the
backlog.

### 7.5 Staging: fetch on demand

Before any job runs, the **stager** gets every input blob into the local cache:

1. **Cache hit:** the blob is `present` in the cache → pin it for the job.
2. **Choose a source:** reachable locations holding a `present` replica, in
   `read_priority` order (cache → landing → NFS → S3 hot → S3 cold → `rig:<name>`).
   Replicas marked `corrupt` are skipped.
3. **Cost and size guards:** if the bytes to download from S3 exceed
   `max_auto_download_gb`, raise `FETCH_APPROVAL_NEEDED`. It shows the size, the estimated
   egress cost, and the reason (for example "re-reference M31: 412 frames, 21.4 GB"). The
   job waits (`waiting_data`) until `altair storage approve <issue>`.
4. **Cold objects:** `GLACIER`, `DEEP_ARCHIVE`, and Intelligent-Tiering archive tiers need
   a restore first:
   - One `RestoreObject` request goes out per needed blob (tier and days from config),
     batched across the whole job. The job is `waiting_data`, and an info issue
     `RESTORE_IN_PROGRESS` shows the tier and a rough ETA. Bulk is typically ≤12 h for
     Glacier Flexible Retrieval and ≤48 h for Deep Archive; Standard is typically 3–5 h
     and ≤12 h.
   - A poller checks `HeadObject` (`x-amz-restore`) every 30 min, and downloads each blob
     as its restore completes.
   - If the total restore exceeds `max_auto_restore_gb`, a `RESTORE_APPROVAL_NEEDED` issue
     is raised before anything is requested.
   - `GLACIER_IR` is read directly, with no restore step (it costs more per GB
     retrieved).
5. **Download and verify:** parallel, resumable (HTTP range) downloads to
   `cache\tmp\`, with a streaming SHA-256 check. A mismatch marks that source replica
   `corrupt`, raises `INTEGRITY_MISMATCH`, schedules re-replication from a good copy, and
   tries the next source. On success the file is atomically renamed into
   `cache\blobs\<aa>\<sha256>.<ext>` and marked read-only.
6. **No source available:** raise `DATA_UNAVAILABLE` (blocking). It lists the blobs, their
   last known locations, and whether the rig PC might still have them (in which case a
   re-delivery request goes out automatically). The job stays in `waiting_data` and
   re-queues automatically when any location becomes reachable again (locations are
   probed every 10 min) or when a replica is discovered.
7. **Hand-off:** inputs are **hard-linked** (NTFS, same volume) into
   `work\<job-id>\inputs\` under their original file names. This copies no data and keeps
   WBPP logs readable.
8. **Prefetch:** staging starts as soon as a job is planned, even while other jobs occupy
   PixInsight. Restores for reruns and re-references are requested right away.

**Cache eviction:** least-recently-used eviction of unpinned blobs, down to
`max_size_gb` and `min_free_gb`. Never evicted: inputs of queued or running jobs, pinned
data classes, and any blob whose **only verified replica is the cache** (for example, a new
night master not yet uploaded). If the cache can't make room, `CACHE_FULL` is raised.

### 7.6 Reclaiming landing space

Landing (and each rig PC's local disk, §7.2) is the only place Altair deletes from. A
landing replica is **reclaimable** only if **all** of these hold:

- It is older than `min_age_days`.
- It has at least `require_durable_copies` durable replicas that are `present` and
  verified by `sha256_full` or `s3_checksum_sha256`. `size_only` counts only under the
  external-backup opt-ins in §7.4.
- Its night has been processed (stacks succeeded or were waived), if `require_processed`.
- It is not referenced by an open issue (`keep_if_open_issue`). This keeps a blocked
  night's lights close by for a fast rerun once the flats arrive.
- It is not an input to a queued job.

Reclaim is **two-phase**. First, mark candidates and **re-check each durable replica right
before deleting** (a fresh `HeadObject` or `stat`). Then delete the landing copy, mark the
replica `missing` (reason `reclaimed`), and append an entry to the reclaim ledger. It runs
daily when landing free space is below `target_free_percent`, oldest night first.
`altair storage reclaim --dry-run` shows what would be deleted.

**Deletions outside Altair** (you, a NAS cleanup job, or another tool): the next rescan
marks those replicas `missing`. If a `raw` blob now has **no** verified durable replica,
`DATA_AT_RISK` is raised immediately, and a re-delivery request is sent to the rig PC if it
might still have the file.

### 7.7 Integrity scrubbing

Every `scrub_interval_days`, a random `scrub_sample_percent` of replicas in each durable
location is re-verified. For S3 that means a checksum `HeadObject`, plus a full download
for 10% of the sample. For file systems it means a full re-hash. Corrupt replicas are
re-replicated from a good copy, and `INTEGRITY_MISMATCH` is raised.

### 7.8 Catalog backup & disaster recovery

- **Nightly catalog backup:** SQLite online backup → zstd → stored as a
  `catalog_backup` blob in every durable location. Kept: 30 daily and 12 monthly.
- **Self-describing archive:** manifests (with headers), calibration master sidecars,
  `night.json`, and multi-night sidecars are all stored next to the data under the same
  logical paths. If every catalog backup were lost,
  `altair storage rebuild-catalog --from s3` would rebuild everything from a bucket
  listing, the manifests, and the sidecars, **without downloading any images**: blobs,
  replicas, frames, calibration masters, projects, night masters, and multi-night
  history. Issues are not restored; they are re-derived by a re-plan.
- **Losing the processing PC:** install Altair on the new PC, restore `altair.yaml`, run
  `altair storage restore-catalog --latest`, and start. The cache starts empty, and
  everything is fetched on demand as jobs need it.
- The disaster-recovery drill (restore on a clean machine and run one merge) is an exit
  criterion for Phase 8.

### 7.9 Adding the NFS later

1. Mount or share it, set `enabled: true` on the `nfs` location, and choose
   `durable: true` or `false` (only durable locations count toward reclaim).
2. `altair storage replicate --to nfs --classes raw,calibration_master,… --dry-run` shows
   how many bytes come from landing and how many must come from S3, with the estimated
   egress. Run it without `--dry-run` to backfill.
3. With `read_priority` 20 (ahead of S3), reruns and re-references are then served from
   the NFS, and S3 becomes purely a disaster backup.
4. If the NFS itself is the landing zone, point `landing.root` at it and keep
   `reclaimable: false`.

No catalog migration is needed. It only adds a location and replicas.

### 7.10 Physical layout

```
\\nas\astro\
├── Landing\                                   # location "landing" (agents write here)
│   ├── _agents\<rig>.json                     # heartbeats
│   ├── _acks\<rig>\<night>.json               # "durable copies verified" acknowledgements
│   ├── _requests\<rig>.json                   # re-delivery requests
│   └── raw\<rig>\<night>\...                  # = logical paths (incl. _manifests\)
└── Masters\                                   # published viewing copies + ALTAIR_STATUS.html

s3://my-astro-archive/altair/                  # location "s3" — same logical tree
├── raw\... calibration\... projects\... catalog\...

Processing PC
E:\AltairCache\                                # location "cache"
│   ├── blobs\<aa>\<sha256>.<ext>              # content-addressed, read-only
│   └── tmp\                                   # in-flight downloads
E:\AltairWork\<job-id>\inputs\                 # hard links into the cache (same volume)
C:\ProgramData\Altair\{altair.yaml, state\altair.db, state\logs\}

Rig PC
D:\NINA\...                                    # NINA save folder (location "rig:<name>")
C:\ProgramData\Altair\agent\{agent.yaml, journal.db, logs\}
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
- Registered frames are data class `registered_frame` (§7.3). Durable replication is
  optional for them. If they are lost, they are regenerated by re-running that night's
  stack against the **same** reference version. That is the one recovery case where a
  night's registration is recomputed.

### 9.7 Re-reference (explicit only)

`altair project rereference <project> [--from-night N]` picks a new reference (for
example, the first night was poor). It increments `reference_version`. All night masters
become `STALE_REFERENCE` and are reprocessed from raw lights, which the stager fetches from
wherever they now live (§7.5). The command first prints the total input size and where
it will come from (for example "412 frames, 21.4 GB: 3.1 GB from landing, 18.3 GB from S3
of which 12.0 GB needs a Glacier restore"). The fetch goes through the normal approval
guards. This is the only operation that repeats registration for existing nights, and it
never happens automatically.

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
| `DELIVERY_INCOMPLETE` | blocking | that rig-night | the missing manifest files arrive (agent retries) |
| `DELIVERY_CORRUPT` | blocking | those files | a later delivery verifies |
| `MANIFEST_MISSING` | warning | nothing (the night is processed from a rescan) | a manifest arrives |
| `RIG_AGENT_OFFLINE` / `RIG_DELIVERY_BACKLOG` | warning | nothing directly | heartbeat fresh / backlog cleared |
| `LOCATION_UNREACHABLE` | warning | fetches from that location | the probe succeeds |
| `DATA_UNAVAILABLE` | blocking | jobs needing those blobs | a replica becomes reachable, is discovered, or is re-delivered |
| `RESTORE_IN_PROGRESS` | info | jobs needing those blobs (they wait) | restore completes and the download verifies |
| `FETCH_APPROVAL_NEEDED` / `RESTORE_APPROVAL_NEEDED` | blocking | that job | `altair storage approve <id>` (or `deny`, which cancels the job) |
| `INTEGRITY_MISMATCH` | warning | nothing (another copy is used) | the bad replica is re-replicated and verified |
| `DATA_AT_RISK` | blocking (alerts immediately) | landing reclaim | a verified durable replica exists |
| `CACHE_FULL` | blocking | staging | space freed or cache limit raised |

Severity `info` issues show on the status page but don't send a push notification
(except `RESTORE_IN_PROGRESS`, which sends one message when it opens and one when it
finishes).

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
before any equipment change. NINA and the agent deliver them as usual.
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
 issue opened ──► user takes flats in NINA ──► agent delivers + manifest ──► ingest
      ▲                                                                   │
      │                                  CALIB_MASTER builds master flat ◄┘
      │                                                  │
      │               planner: does any open issue's requirement_json match?
      │                           │ yes
      │                           ▼
      │      NIGHT_STACK rerun for blocked night(s) (stager fetches raw lights
      │      from landing / NFS / S3, restoring from Glacier if needed)
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
- **Raw data for reruns:** held lights stay linked to their issue, and landing reclaim
  skips them while the issue is open (`keep_if_open_issue`). If they have been removed
  anyway, for example by an outside cleanup or because the setting is off, the stager
  fetches them from NFS or S3, restoring from Glacier if needed, and the issue's status
  line shows "waiting for restore, ETA …". The rerun is only slower, never impossible.
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
- **Raw data is never lost.** Raw files are never moved or modified. A landing copy is
  deleted only by reclaim (§7.6), and only when a verified durable copy exists.
- **Resumable transfers:** agent copies, uploads, downloads, and restores are all
  identified by blob hash and are idempotent. After a crash they resume (S3 multipart
  resume, HTTP range) or restart cleanly. Nothing half-transferred is ever marked
  `present`.
- **Storage workers are independent of PixInsight:** replication, staging, restore
  polling, reclaim, and scrubbing run in their own worker pool with their own
  concurrency limits. A multi-hour Glacier restore or a slow upload never blocks
  processing of data that is already available.

---

## 12. Interfaces

### 12.1 CLI (`altair`)

```
altair serve [--windowless]
altair agent serve | install | uninstall            # rig PC service
altair agent signal session-end                    # called by NINA's External Script
altair agent status | redeliver --night DATE | config export --rig R
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
altair storage status                             # per location: reachable, bytes, replicas, pending uploads, at-risk count
altair storage locate <sha256|logical-path|--night DATE --rig R>   # every replica + state
altair storage fetch <selector> [--dry-run]       # pre-stage into cache (shows sizes, sources, restore needs)
altair storage approve|deny <issue-id>            # approve a large fetch or restore
altair storage replicate --to LOC [--classes ...] [--dry-run]      # backfill, e.g. after adding NFS
altair storage reclaim [--dry-run]                # landing reclaim per §7.6
altair storage scrub [--location LOC] [--sample PCT]
altair storage index --location s3                # discover replicas (external-managed buckets)
altair storage backup-catalog | restore-catalog [--latest|--at TIME] | rebuild-catalog --from LOC
altair publish --refresh
altair doctor                                     # PixInsight/CLI flags, session type, paths, disk, NINA headers sample,
                                                  # share/S3 access, bucket versioning, agent heartbeats
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
| **1: Agent, delivery & catalog ingest** | `altair agent` service (stability, hashing, read-back verified copy, journal, manifests with headers, heartbeat, local retention). Processing-side ingest: config, header mapping, rig and rotator extraction, blob and replica model, SQLite schema. | A real NINA night delivered from a rig PC indexes correctly from its manifest. Pulling the network cable mid-night loses nothing, and delivery completes after reconnecting. Tests cover rotator parsing and wrap-around. |
| **2: Planner & matching** | §8 rules, including rotator and equipment events, issue generation, `altair plan`. | Table-driven tests for every rule: rotator Δ at tolerance ±1 step, wrap-around, missing position, event between flat and light. |
| **2b: Storage manager** | §7: replicator (S3 multipart + SHA-256 checksums, file-system copy), stager (source selection, verify, hard-link hand-off, cache eviction), Glacier restore flow, approval guards, reclaim, scrub, catalog backup and restore. | Against MinIO / S3: delete landing → a job still runs from S3. Deep-archive objects → restore → run. A corrupted replica is detected and bypassed. Reclaim never deletes a blob with no verified durable copy (property test). `rebuild-catalog` from the bucket alone reproduces the catalog. |
| **3: Night stacks** | Executor (Job Objects, timeouts), `PROJECT_REFERENCE`, `NIGHT_STACK`, verifier, publisher, archive. | A night master matches a manual WBPP run on the same data within noise, and is pixel-aligned with the reference. |
| **4: Calibration library** | `CALIB_MASTER`, rotator-tagged master flats, import, supersession. | A flats-only night produces masters that are matched automatically. |
| **5: Merger** | §9: gates, measured weighting, LN, coverage-aware integration, versions, reports. | A 3-night merge's weights match the measured PSF signal ordering. Excluding a night and re-including it reproduces the same result byte for byte. Uncovered edges don't darken the result. |
| **6: Issues & rerun loop** | §10: issue store, dedup, toast, push, email, status page, auto-rerun on calibration arrival, manual resolve and waive. | End to end: a night without SII flats → issue + toast → take matching flats in NINA → agent delivers → automatic rerun (with raw lights fetched from S3 if landing was reclaimed) → merge → "resolved" notification, with no CLI use. |
| **7: Daemon & hardening** | Triggers (manifests, scheduled fallback), Task Scheduler install script (processing PC), agent service installer (rig PCs), sleep prevention, crash recovery, `altair doctor`. | A week of unattended real nights from two rigs. Survives killing `altaird`, the agent, and `PixInsight.exe` mid-job. |
| **8: Disaster-recovery drill** | Documented runbook. | On a clean machine, with only `altair.yaml` and S3: `restore-catalog`, then produce a multi-night master update for one project. |

### 15.1 Proposed source layout

```
altair-pre-processor/
├── pyproject.toml
├── altair.example.yaml
├── src/altair/
│   ├── cli.py  config.py  daemon.py
│   ├── agent/        # service.py, watcher.py, deliver.py, journal.py, manifest.py, heartbeat.py
│   ├── triggers/     # manifests.py, schedule.py, dawn.py
│   ├── ingest/       # headers.py, nina.py, rotator.py, normalize.py, classify.py
│   ├── storage/      # blobs.py, locations/{fs.py, s3.py}, replicator.py, stager.py, restore.py,
│   │                 # cache.py, reclaim.py, scrub.py, catalog_backup.py, rebuild.py
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
│   ├── install-task.ps1          # registers the Task Scheduler task (processing PC)
│   ├── install-agent.ps1         # installs the agent Windows service (rig PCs)
│   └── nina-external-script.md
├── tests/
│   ├── fixtures/                 # synthetic NINA-style FITS (astropy), tiny images
│   ├── test_rotator.py  test_matching.py  test_gates.py  test_issues.py
│   ├── test_weights.py           # weighting math on synthetic noise/signal
│   ├── test_storage_*.py         # replicator, stager, restore, reclaim invariants (moto + MinIO)
│   ├── test_agent.py             # stability, partial writes, share outage, manifests
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
- **Storage:** unit tests against `moto` (an S3 mock, which supports storage classes and
  restore semantics) and integration tests against a local MinIO. Fault injection covers
  truncated downloads, flipped bytes, unreachable locations, deleted landing files, and
  restores that never finish. **Property tests** on reclaim assert that no sequence of
  operations ever leaves a `raw` blob with zero verified replicas.
- **Contract:** a fake `PixInsight.exe` (a small Python-built exe or `.cmd` shim) that
  validates `job.json` and emits canned outputs. This runs in CI on `windows-latest`.
- **Integration (local, marked):** real PixInsight on a small reference dataset of 2
  nights, 2 filters, and one rotator change, with one night deliberately missing flats.
  Golden checks are statistical (median, MAD, star count, weight ordering), not
  byte-for-byte.
- **Soak:** replay a recorded NINA night's file arrivals into a rig PC's NINA folder,
  with the agent delivering over a real SMB share.

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
| R7 | An external backup tool that mirrors deletions would wipe the S3 copy when landing is reclaimed. | Recommend `managed_by: altair`. Reclaim with an external backup requires explicit opt-ins (§7.4). `doctor` checks bucket versioning. |
| R8 | Glacier restore latency (hours) and retrieval or egress costs on reruns and re-references. | Prefetch as soon as a job is planned. Approval guards with cost estimates. `keep_if_open_issue` keeps likely rerun inputs on landing. The NFS (if added) serves reruns first. |
| R9 | Losing the catalog makes the archive hard to navigate. | Nightly catalog backups to every durable location, plus the self-describing archive (manifests with headers, sidecars) and `rebuild-catalog`. |
| R10 | SMB silent corruption or partial writes. | SHA-256 at the source, read-back verification, `.partial` + rename, and hash checks on every fetch. |
| Q1 | Is the rotator reported in degrees or steps, and is steps-per-revolution known? Which rotator and driver? | Sets the rig's `rotator` defaults. |
| Q2 | ~~Same PC or separate?~~ **Decided:** one mini PC per rig, a separate processing PC, and a shared folder. | §3.0, §4.1. |
| Q5 | S3 provider (AWS / B2 / Wasabi / other) and intended storage classes for raw data (Standard-IA, Glacier IR, Deep Archive)? | Sets `storage_class`, restore behaviour, and cost guards. |
| Q6 | Is today's S3 backup done by an existing tool (which one?), and does it mirror deletions? Or should Altair own the uploads? | Chooses `managed_by`. External is supported, but its reclaim safety depends on the answer. |
| Q7 | NFS: add it, and should it count as durable (RAID, snapshots)? | With a durable NFS, landing can be reclaimed aggressively and S3 becomes disaster-only. |
| Q8 | Network speed between the processing PC and the NAS, and the internet uplink and downlink? | Sizes staging expectations, upload windows, and restore-to-run ETAs. |
| Q3 | Should the multi-night master require every night to cover the full frame (strict crop), or allow partial-coverage edges? | Default: crop to full coverage (`min_coverage_nights: all`). |
| Q4 | Default weighting: `measured_psf_signal` or `inverse_noise_variance`? | Spec default is PSF signal (it rewards seeing and transparency too). Phase 5 compares both on real data. |
