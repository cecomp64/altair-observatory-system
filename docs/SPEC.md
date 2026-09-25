# Altair Pre-Processor — Implementation Specification

**Status:** Draft v0.5
**Date:** 2026-09-25
**Target platform:** Windows 10/11 (x64). Each telescope has its own NINA mini PC. A
separate, more powerful processing PC runs PixInsight 1.9.x with WBPP 2.x and reads each
rig PC's NINA folder over the network. Backup goes to Amazon S3, plus an optional NFS
archive.

### Changelog

| Version | Changes |
|---|---|
| v0.5 | Removes the separate shared folder and the rig PC agent. Each rig PC's own NINA folder, exposed as a network share, is the raw data source. The processing PC has a config entry per rig (host, raw data location, credentials, collection settings). A **collector** on the processing PC pulls frames from each rig as they are written, verifies them with a double read, and backs them up first. Rig PC cleanup is done by the processing PC over the share. (§2–§7, §10–§16) |
| v0.4 | Altair owns the S3 backup (Amazon S3) and the NFS archive copy. Raw lights are backed up **first**, as each frame lands. Backup only copies and never syncs deletions, which the IAM policy enforces. What gets backed up: raw lights, calibration masters, reference frames, calibrated light subs, night and multi-night masters, and metadata. Not backed up: registered subs and raw calibration subs. Adds retention and cleanup across rig PCs, landing, cache, work, published, and logs, with a deletion ledger. Backup is now the first build phase. (§3, §5, §6, §7, §9.6, §9.7, §10, §11, §15, §16) |
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
- **A night's raw data is never lost:** raw lights are backed up to Amazon S3 (and the
  local NFS archive, if present) as soon as the processing PC collects them from the rig,
  during the night, before any processing. No copy of a raw
  light is deleted anywhere until that backup is verified, and backup never copies
  deletions.
- **Works wherever the data lives:** jobs ask for files by content hash, not by path. Any
  file the pipeline needs, including raw lights from months ago for a rerun or
  re-reference, is fetched automatically from whichever storage location still has it
  (the rig PC, the processing PC's cache, the NFS, or S3 including Glacier), and checked against
  its hash before use. Deleting local copies is safe because nothing is deleted unless a
  verified backup exists.
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
| **Rig PC** | The NINA mini PC attached to one telescope. It runs only NINA. Its NINA save folder is shared on the network so the processing PC can read it. Nothing from Altair is installed on it except a one-line session-end script (§4.2). |
| **Rig raw root** | The network path of a rig PC's NINA save folder (for example `\\rig-esprit\NINA`), set per rig in the processing PC's config (§5). It is where that rig's raw data lives, and the only place it lives until collected. |
| **Processing PC** | The machine that runs `altaird` (with its collector) and PixInsight. |
| **Collector** | The part of `altaird` that pulls new frames from each rig's raw root into the processing PC, verifies them, and hands them to ingest and backup (§7.3). |
| **Blob** | One file's content, identified by its SHA-256 hash. The catalog refers to data only by blob hash plus a *logical path*, never by a physical path. |
| **Location** | A configured place blobs can live: `rig:<name>` (a rig PC's NINA folder, one per rig), `cache` (processing PC SSD), `nfs`, or `s3`. |
| **Replica** | One copy of a blob in one location. It is tracked with state (`present`, `missing`, `archived_cold`, `restoring`) and when it was last verified. |
| **Durable location** | A backup location: S3 (always) and the NFS archive (if enabled). Rig PCs and the processing PC cache are **not** durable. |
| **Backup** | Altair's own **copy-only** replication of selected data classes to S3 and the NFS (§7.4–7.5). Deletions are never synced. |
| **Cleanup** | Altair's retention rules that delete **individual copies** in non-archive locations, and only once a verified backup exists (§7.6). |
| **Staging** | Making sure every input blob of a job is in the local cache, verified, before PixInsight starts. |

---

## 3. High-Level Architecture

### 3.0 Physical topology

```
 ┌── Rig PC A (mini PC) ─────────┐     ┌── Rig PC B (mini PC) ─────────┐
 │ NINA → D:\NINA (local disk)    │     │ NINA → D:\NINA (local disk)    │
 │ shared as \\rig-esprit\NINA     │     │ shared as \\rig-rasa\NINA       │
 │ location rig:esprit100_2600mm  │     │ location rig:rasa8_533mc       │
 └───────────────┬───────────────┘     └───────────────┬───────────────┘
                 │ SMB (processing PC pulls: read + verify; later cleanup deletes)
                 └──────────────────┬──────────────────┘
                                    ▼
      ┌──────────────────────────────────────────────────┐   upload   ┌────────────────────┐
      │ Processing PC: altaird (collector, backup,        │──────────►│ Amazon S3 (durable) │
      │ planner, stager, …) + PixInsight                  │  (raw      │ STANDARD / IA /     │
      │ local SSD cache (location:cache)                  │   first)   │ GLACIER tiers       │
      │ catalog DB (backed up to S3 nightly)              │◄───────────┤                     │
      └───────────────┬──────────────────────────────────┘  fetch     └────────────────────┘
                      │ copy / fetch
                      ▼
            ┌───────────────────────┐
            │ NFS archive (optional, │
            │ durable)               │
            └───────────────────────┘
```

- **Rig PCs** only capture. NINA saves to the rig PC's **local disk**, so a network outage
  never costs frames. The NINA save folder is shared on the network. That share is the
  rig's raw data location, and the processing PC has one config entry per rig pointing at
  it (§5).
- The **processing PC** pulls each new frame from each rig while the night is still going
  (§7.3). It verifies the frame, then backs it up to S3 (and the NFS) before anything else
  (§7.2). PixInsight never reads a network path. Every job input is staged into the local
  SSD cache first (§7.7).
- **S3** (and the NFS, if you add it) is the durable archive. A rig PC's copy of a frame is
  deleted by the processing PC's cleanup only after the backup is verified (§7.6).

### 3.1 Software components

```
           ┌───────────────────────────────────────────────────────────────────────┐
           │                     altaird (Processing PC, Windows)                   │
 rig raw   │ 0 Collector ─► 2 Ingest & ─► 3 Planner ─► 4 Stager ─► 5 Executor ─►    │
 roots ──► │   (pull, verify) Catalog     (group,      (fetch     (PixInsight       │
 (per-rig  │ 1 Trigger       (headers,    calib/flat   inputs to   WBPP, per        │
 config)   │   (session end,  blobs,      matching)    cache)      night)           │
           │   schedule)     replicas)        │            ▲            │           │
           │                                 │            │            ▼           │
           │   Calibration Library ◄─────────┤   Storage Manager   6 Verifier &     │
           │                                 │   (BACKUP first:    Publisher        │
           │                                 │    raw → S3/NFS on      │            │
           │                                 │    ingest; restore,     ▼            │
           │                                 │    cleanup)        7 Merger         │
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
process. Nothing from Altair runs on the rig PCs.

### 3.2 Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Content identity | SHA-256, computed by the collector when it first reads a frame from the rig, and confirmed by a second independent read (§7.3) | A hash that stays the same across every location. It catches corruption in any copy, download, or restore. |
| Object storage | `boto3` against any S3-compatible endpoint (AWS, Backblaze B2, Wasabi, MinIO) | One interface for AWS and self-hosted storage. Storage-class and restore aware. |
| Orchestrator | Python 3.12+, shipped as a venv or a PyInstaller-built `altair.exe` | Mature FITS tooling, easy subprocess and Win32 control. |
| Header I/O | `astropy.io.fits`, plus a small XISF header reader | NINA can save FITS or XISF. |
| Rig folder watching | Polling each rig's raw root (a directory scan every `poll_interval_s`), optionally sped up by `watchdog` change notifications over SMB | Change notifications over SMB are unreliable on their own. Polling is the source of truth, and notifications only make it react faster. |
| Win32 integration | `pywin32` / `psutil` | Job Objects for killing the PixInsight process tree, `SetThreadExecutionState` to keep the PC awake, exclusive-open checks, toast notifications. |
| State store | SQLite (WAL mode) | Single host, transactional, needs no server. |
| Config | YAML validated with `pydantic` | Typed, and bad config fails at startup. |
| Job execution | PixInsight CLI in automation mode running PJSR wrapper scripts | Required by the project goal. |

---

## 4. Windows & NINA Environment

### 4.1 Deployment model

**Rig PCs.** Only NINA runs here. Setup for each rig PC:

- Share the NINA save folder (for example `D:\NINA` as `\\rig-esprit\NINA`) to one
  dedicated Windows account that the processing PC uses. Give it **Modify** permission
  (read, plus delete for cleanup, §7.6). If you'd rather the processing PC never delete
  on the rig, grant **Read** only and set `cleanup.enabled: false` for that rig. Then you
  clean up the rig by hand, guided by `altair storage cleanup --dry-run`.
- Add the session-end script to the NINA sequence (§4.2).
- Keep the rig PC from sleeping, and give it enough disk for several nights. Its copy is
  the only copy until the processing PC collects it.

The processing PC stores each rig's share credentials in Windows Credential Manager
(`credential_target` in the rig config). `altair doctor` checks from the processing PC
that every rig's raw root is reachable and writable (for cleanup) with those credentials.

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
processing never competes with acquisition. Collection during the night only reads
finished frames, throttled by `collect.bandwidth_limit_mbps` if needed, so it has
negligible impact on the rig PC. A single-PC setup (NINA and `altaird` together) is still
supported: that rig's `raw_root` is simply a local path, and processing only starts after
the session ends, at `BELOW_NORMAL` priority.

**Keeping the PC awake:** while any job is queued or running, `altaird` calls
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`. `altair doctor` warns if
Windows Update active hours overlap the configured processing window.

**Process control:** each PixInsight run is assigned to a **Windows Job Object** with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. On timeout or crash, the whole process tree is killed
reliably and no orphaned `PixInsight.exe` is left holding the instance slot.

**Performance notes (checked by `altair doctor`):** PixInsight swap directories should be
on a fast SSD. The `cache`, `work`, and `projects` directories should be excluded from
Windows Defender real-time scanning. Long-path support should be enabled
(`LongPathsEnabled=1`), because WBPP output paths get deep. Wired Ethernet to each rig PC
is recommended. At 1 GbE, a 100-frame night (about 5 GB of 16-bit frames, read twice for
verification) collects in about 2 minutes. Over Wi-Fi it takes longer, but it happens
frame by frame during the night.

### 4.2 NINA integration

**Where NINA saves.** NINA saves to a folder on the rig PC's **local disk** (for example
`D:\NINA`). That folder is shared, and its network path is the rig's `raw_root` in the
processing PC's config.

**Session-end signal.** In the NINA Advanced Sequencer, add an **External Script**
instruction to the sequence's *End* area (and optionally after each target's flats). It
runs a tiny script on the **rig PC** (`deploy/windows/altair-session-end.cmd`, copied
there once). The script only writes a marker file into the NINA folder:

```
D:\NINA\_altair\session-end-<local timestamp>.json    { "host": "...", "at": "..." }
```

The collector sees the marker on its next poll. It collects any remaining frames, and
once every frame of that night has been collected and verified, the night is ready for
processing (§6.1). If the sequence aborts or NINA crashes and no marker is written, the
rig's quiescence rule (no new frames for `quiescence_minutes`, after dawn) closes the
night instead, and the daily scheduled run (§6.1) is the last safety net.

**Recommended NINA image file pattern** (Options → Imaging). It is not required, because
Altair relies on headers, but it keeps folders readable. The collector keeps this relative
path as part of each frame's logical path (`raw/<rig>/<relative path>`):

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

A single `altair.yaml` on the processing PC (default
`C:\ProgramData\Altair\altair.yaml`), validated at startup. Rig PCs have no Altair
configuration. Everything about a rig, including **where its raw data is**, how to reach
it, and how to collect and clean up, lives in that rig's entry under `rigs:`.

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
  published: "D:/Astro/Masters"           # user-facing copies of masters (convenience, not the record); share it if you like

storage:                                  # see §7
  cache:
    path: "E:/AltairCache"                # location "cache": processing PC SSD
    max_size_gb: 800
    min_free_gb: 100
    pin: [calibration_master, project_reference, night_master, multi_night_master]
    pin_raw_until: [durable_verified, night_processed, no_open_issue]   # collected raw frames stay local until all hold
  locations:                              # rig locations (rig:<name>) come from `rigs:` below, read_priority 10
    - name: nfs
      kind: fs
      root: "//bignfs/astro-archive"      # a UNC path works for SMB or the Windows NFS client
      durable: true
      read_priority: 20
      enabled: false                      # turn on if/when the NFS is added; backfill with `altair storage replicate`
      backup_classes: same_as_s3          # or an explicit list (e.g. add raw_calibration here — NFS space is cheap)
    - name: s3
      kind: s3                            # Amazon S3; Altair does all uploads itself
      bucket: "my-astro-archive"
      prefix: "altair/"
      region: "us-west-2"
      credentials: { profile: "altair" }  # AWS profile for a dedicated least-privilege IAM user (§7.5)
      durable: true
      read_priority: 30
      backup_classes:                     # what is copied here (§7.4); anything not listed is never uploaded
        - raw_light
        - calibration_master
        - project_reference
        - calibrated_frame
        - night_master
        - multi_night_master
        - metadata                        # manifests, sidecars, catalog backups (small)
      storage_class:                      # applied on upload
        raw_light: STANDARD_IA
        calibrated_frame: STANDARD_IA
        default: STANDARD
      lifecycle:                          # applied to the bucket by `altair storage s3 apply-lifecycle` — transitions only, never expiry of kept data
        raw_light:        { to: DEEP_ARCHIVE, after_days: 120 }
        calibrated_frame: { to: GLACIER_IR,   after_days: 60 }
        abort_incomplete_multipart_after_days: 7
      restore:
        tier: Bulk                        # Bulk | Standard | Expedited
        days: 7
        max_auto_restore_gb: 50           # above this, a RESTORE_APPROVAL_NEEDED issue is raised
      max_auto_download_gb: 200           # above this, approval is required (egress cost guard)
      upload_bandwidth_limit_mbps: null
      upload_window: null                 # e.g. "00:00-23:59"; raw_light ignores the window (§7.5)
      multipart_chunk_mb: 64

  backup:
    raw_first: true                       # raw_light uploads pre-empt every other transfer
    start: on_collect                     # upload each frame as soon as it is collected from the rig, during the night
    processing_waits_for_raw_backup: false   # true = a night is not processed until its raw lights are durable
    raw_backup_sla_hours: 6               # BACKUP_BEHIND alert if a collected raw light is not durable by then
    calibrated_frame_stage: calibrated    # calibrated | cosmetized | debayered — which WBPP intermediate is kept (§7.4)
    calibrated_frame_compression: zstd+shuffle   # XISF lossless compression before upload; none to disable

  cleanup:                                # §7.6 — every rule is per location; deletions never propagate between locations
    schedule_local: "10:30"
    dry_run: false
    rig_defaults:                         # applied to every rig's NINA folder; override per rig under rigs.<name>.cleanup
      enabled: true
      raw_light:       { min_age_days: 7, require: [collected_verified, durable_verified] }
      raw_calibration: { min_age_days: 7, require: [collected_verified, master_durable] }
      target_free_percent: 20             # below this free space, clean oldest-first before min_age (the require rules still apply)
      keep: ["_altair/**"]                # never touched (session-end markers are cleaned by their own rule below)
      markers_keep_days: 30
    cache:
      evict: lru                          # §7.7; never evicts pinned or sole-copy blobs
    work:
      succeeded_jobs: delete_immediately
      failed_jobs_keep_days: 7
    published:
      night_masters_keep_days: 365        # viewing copies only; the canonical masters stay archived
      multi_night_versions_keep: 3
    logs_keep_days: 90
    nfs:                                  # archive: nothing is deleted unless you add rules here
      {}
    s3:                                   # archive: kept data classes are NEVER deleted by Altair
      multi_night_master_versions_keep: 20   # older superseded versions deleted (regenerable from night masters)
      catalog_backups_keep: { daily: 30, monthly: 12 }

  verify:
    s3_after_upload: checksum             # checksum (SHA-256 stored + S3 checksum) | head_only
    scrub_interval_days: 90               # re-verify a random sample of replicas
    scrub_sample_percent: 2

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
  min_lights_per_stack: 5

rigs:
  esprit100_2600mm:
    # ── where the raw data is and how to reach it ──
    host: "rig-esprit"                    # rig PC network name (used for reachability probes)
    raw_root: "//rig-esprit/NINA"         # the rig PC's shared NINA save folder = location rig:esprit100_2600mm
    credential_target: "altair-rig-esprit"   # Windows Credential Manager entry with the share account
    include: ["**/*.fits", "**/*.fit", "**/*.xisf"]
    exclude: ["_altair/**", "**/*.tmp", "**/Snapshots/**"]
    # ── collection (§7.3) ──
    collect:
      poll_interval_s: 60                 # directory scan cadence (SMB change notifications only speed this up)
      stable_seconds: 60                  # size + mtime unchanged this long, and exclusive open succeeds
      during_capture: true                # false = only collect after the session-end marker / quiescence
      verify: double_read                 # double_read (default) | single_read
      bandwidth_limit_mbps: null
      parallel_files: 2
    session_end_marker: "_altair/session-end-*.json"   # written by the NINA end-of-sequence script (§4.2)
    quiescence_minutes: 45                # no marker: night closes after this long with no new frames (after dawn)
    unreachable_alert_minutes: 30         # RIG_UNREACHABLE warning; escalates to blocking after 12 h
    # cleanup: { raw_light: { min_age_days: 14 } }   # optional per-rig overrides of storage.cleanup.rig_defaults
    # ── optics ──
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
    raw_root: "//rig-rasa/NINA"
    credential_target: "altair-rig-rasa"
    collect: { poll_interval_s: 60, stable_seconds: 60, during_capture: true, verify: double_read }
    session_end_marker: "_altair/session-end-*.json"
    quiescence_minutes: 45
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

1. **Session-end marker (primary).** The NINA end-of-sequence script writes
   `_altair\session-end-*.json` in the rig's NINA folder (§4.2). On its next poll the
   collector does a final full scan of that rig's raw root. It collects every remaining
   stable frame of the night and writes the night's **collection manifest** (§7.3). The
   night is **ready** once every frame found in the rig's folder for that night has been
   collected and verified. A frame that stays unstable or unreadable for more than 30
   minutes after the marker raises `COLLECTION_STUCK`.
2. **Quiescence.** No marker, but no new frame from that rig for `quiescence_minutes`, and
   it's after dawn (if `require_after_dawn`). This covers aborted sequences and NINA
   crashes. The night closes the same way, with a warning (`SESSION_END_MARKER_MISSING`).
3. **Scheduled fallback.** A daily run at `scheduled_fallback_local` closes any night that
   is still open, provided its rig is reachable. A night whose rig is unreachable stays
   open, and `RIG_UNREACHABLE` explains why.
4. **Calibration arrival.** Newly indexed calibration frames or masters that could resolve
   an open issue (§10) trigger a targeted re-plan.
5. **Data availability.** A blob that was unavailable becomes reachable again (a location
   comes back online, or an S3 restore completes). Jobs waiting on it re-queue (§7.7).
6. **Manual.** `altair run …` / `altair rerun …` / `altair collect --rig R --close-night DATE`.

**File stability and verification** are the collector's job (§7.3). Ingest only sees
frames the collector has copied into the processing PC and verified.

### 6.2 Ingest & Catalog

For each frame the collector has copied and verified (§7.3):

1. Register the **blob** (its SHA-256), its **logical path**
   (`raw/<rig>/<relative path under raw_root>`), and two `present` **replicas**: the
   original in `rig:<name>` and the verified copy in `cache`. If the blob is already known
   (for example, the same file seen again after a rig PC folder move), it is linked to the
   existing blob and never processed twice.
2. Read the headers and resolve canonical fields through `header_mapping` and `aliases`.
3. Resolve the **rig** from (telescope, camera). This is cross-checked against the rig
   whose raw root the frame was collected from. A mismatch raises `UNKNOWN_RIG`. Frames that match no configured rig get
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
8. **Queue the backup right away.** A `raw_light` goes to the front of the backup queue
   (S3, plus the NFS if enabled) the moment it is registered, before any planning or
   processing (§7.2, §7.5). `raw_calibration` blobs are not backed up; they are kept
   until their masters are (§7.4, §7.6).

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
So moving, cleaning up, or restoring data never changes the catalog's view of *what* the
data is. It only changes *where* the data can be fetched from.

```sql
-- ── Storage (§7) ──────────────────────────────────────────────────────────
CREATE TABLE blobs (
  sha256 TEXT PRIMARY KEY,
  size_bytes INTEGER NOT NULL,
  data_class TEXT NOT NULL,      -- raw_light / raw_calibration / calibration_master / project_reference /
                                 -- calibrated_frame / night_master / multi_night_master / registered_frame /
                                 -- provisional / metadata   (backup policy per class: §7.4)
  logical_path TEXT NOT NULL,    -- e.g. raw/esprit100_2600mm/2026-09-24/M31/LIGHT/Ha/..._0001.fits
  origin_rig TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(logical_path, sha256)
);

CREATE TABLE locations (
  name TEXT PRIMARY KEY,         -- rig:<name> / cache / nfs / s3
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
                                 -- (missing replicas keep a reason: cleanup / external_delete / never_arrived)
  storage_class TEXT,            -- S3 only (STANDARD, STANDARD_IA, GLACIER, DEEP_ARCHIVE, ...)
  restore_expires_at TEXT,       -- S3 only
  verified_at TEXT,              -- last time the content was verified against sha256
  verify_method TEXT,            -- sha256_full / s3_checksum_sha256 / size_only
  last_seen_at TEXT,
  PRIMARY KEY (sha256, location)
);

CREATE TABLE fetch_requests (    -- staging and restore bookkeeping (§7.7)
  id INTEGER PRIMARY KEY,
  job_id INTEGER REFERENCES jobs(id),
  sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  source_location TEXT,
  state TEXT NOT NULL,           -- pending / restoring / downloading / done / failed / awaiting_approval
  bytes_done INTEGER DEFAULT 0,
  error TEXT, updated_at TEXT
);

CREATE TABLE cleanup_ledger (    -- every deletion Altair ever performs (§7.6)
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL, location TEXT NOT NULL, uri TEXT NOT NULL,
  rule TEXT NOT NULL,             -- e.g. rig:esprit100_2600mm.raw_light
  relied_on_json TEXT NOT NULL,   -- the verified backup replica(s) checked right before deleting
  deleted_at TEXT NOT NULL, dry_run INTEGER NOT NULL
);

CREATE TABLE collections (       -- one row per rig-night (§7.3)
  rig TEXT NOT NULL, night TEXT NOT NULL,
  state TEXT NOT NULL,           -- open / closing / closed
  closed_by TEXT,                -- session_end_marker / quiescence / scheduled / manual
  n_files INTEGER, total_bytes INTEGER,
  manifest_sha256 TEXT,          -- collection manifest blob (written at close)
  opened_at TEXT, closed_at TEXT,
  PRIMARY KEY (rig, night)
);

CREATE TABLE rig_files (         -- what the collector has seen in each rig's raw root
  rig TEXT NOT NULL, rel_path TEXT NOT NULL,
  size INTEGER, mtime TEXT, first_seen_at TEXT, stable_since TEXT,
  state TEXT NOT NULL,           -- seen / collecting / collected / stuck / excluded / gone
  sha256 TEXT REFERENCES blobs(sha256),
  PRIMARY KEY (rig, rel_path)
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
   where each input would come from (cache, rig PC, NFS, S3 hot, S3 cold). That feeds the
   approval guards and ETAs in §7.7. Planning itself never needs file contents, because
   headers are cached in the catalog.

### 6.5 Executor (PixInsight on Windows)

**Before PixInsight starts, every job is staged** (§7.7). All input blobs are made
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
   queued for backup (§7.5). The **calibrated light subs** of a final (flat-verified)
   stack, at `calibrated_frame_stage`, are collected from WBPP's output before the work
   directory is cleared. They are registered as `calibrated_frame` blobs and queued for
   backup too. Registered subs are **not** kept beyond the job (§7.4).
3. **Publish a viewing copy** (autocropped) to
   `published\<target>\<night>\<target>_<telescope>_<camera>_<filter>_<night>_<N>x<exp>s.xisf`.
   Provisional masters get the suffix `_NOFLAT-PROVISIONAL`. Published copies are for
   convenience only. They are not tracked as replicas and can be regenerated at any time
   (`altair publish --refresh`).
4. **Raw lights are not moved.** They were already backed up at collection (§7.2). The
   rig PC's original stays in the NINA folder until cleanup (§7.6) removes it.
5. **Record the night master** with `merge_status`, set by the gates in §9.4.

### 6.7 Merger

See §9.

### 6.8 Issue Tracker & Notifier

See §10.

---

## 7. Storage, Backup & Data Retrieval

### 7.1 Principles

1. **Backup comes first.** A raw light is uploaded to S3 (and copied to the NFS archive,
   if one is configured) **as soon as the collector has pulled it from the rig**, during
   the night, ahead of every other
   transfer and independent of processing. No copy of a raw light is ever deleted anywhere
   until its backup is verified.
2. **Altair owns the backup.** Altair does all S3 uploads (Amazon S3) and NFS copies
   itself. No external sync tool is needed or assumed.
3. **Backup copies; it never syncs deletions.** Replication only adds files. Deleting a
   file in one location, whether by Altair's cleanup, by you, or by a crashed disk, never
   causes a deletion anywhere else. S3 permissions enforce this at the IAM level (§7.5).
4. **Content-addressed.** Every file is a blob identified by SHA-256. The hash is computed
   when the collector first reads a frame from the rig, and confirmed by a second
   independent read (§7.3), so every later copy, upload, download, and restore can be
   checked against it.
5. **The catalog doesn't depend on where files are.** The catalog says *what* data exists.
   `replicas` say *where* it currently is. Losing a copy only changes where the next fetch
   comes from.
6. **Raw files are never changed or moved.** Archive keys are never overwritten. Every
   output's logical path includes a version or content-hash suffix.
7. **Nothing is deleted without a verified backup.** This is a hard rule with no override:
   Altair never deletes the only verified copy of a blob, and cleanup (§7.6) always checks
   the backup again right before deleting.
8. **PixInsight only reads the local cache**, and **every input can be re-fetched** from
   whichever location still has it (§7.7).

### 7.2 Pipeline order

```
 capture night ─────────────────────────────► dawn ─────────────────────────────► days / weeks
 Rig PC   NINA saves to local D:\NINA (shared)                          session-end marker
 Proc PC  collector pulls each finished frame (≈1 min after it is written) ─┐
          ingest ─► [1] RAW BACKUP → S3 + NFS  (continuous, top priority, verified)
                                     close night + manifest ─► plan ─► stage ─► WBPP ─► verify
                                                                    │
                                        [2] OUTPUT BACKUP → S3 + NFS ◄┘  masters, reference,
                                                                         calibrated light subs
                                                                    [3] CLEANUP (per location,
                                                                        only verified-backed-up data)
```

1. **Raw backup** starts as soon as each frame is collected, and each frame is uploaded
   while the night is still going. By the time the session ends, most of the night is usually already in S3.
   Processing does not have to wait for it, because processing only reads raw files and
   never changes them. If you want a strict order anyway, set
   `processing_waits_for_raw_backup: true`: a night is then not processed until all of
   its raw lights are durable.
2. **Output backup** runs as soon as the verifier accepts a job's outputs.
3. **Cleanup** runs daily. It only removes copies whose backup is verified, following the
   rules for each location (§7.6).

A collected raw light that still has no verified S3 copy after `raw_backup_sla_hours`
raises **`BACKUP_BEHIND`** (warning). A raw light more than 48 h old with **no** verified
durable copy anywhere raises **`DATA_AT_RISK`** (blocking, alerted immediately). Nothing
from that night is cleaned up until the condition clears. Until a frame is collected, its
only copy is on the rig PC, so an unreachable rig raises `RIG_UNREACHABLE` (§7.3).

### 7.3 Collector (rig PC → processing PC)

The collector is part of `altaird`. It runs one worker per configured rig. Rig PCs run
nothing from Altair; the collector does everything over the rig's share, using the rig's
`raw_root` and credentials from the config (§5).

**Per poll (`poll_interval_s`) for each rig:**

1. **Reachability:** probe `raw_root`. If it can't be reached, mark `rig:<name>`
   unreachable. After `unreachable_alert_minutes`, raise `RIG_UNREACHABLE` (warning),
   escalating to **blocking** after 12 h, because a rig's uncollected frames exist
   **only** on that rig. Collection resumes automatically when the rig comes back, and
   nothing is lost in the meantime: NINA keeps writing locally, and nothing uncollected is
   ever cleaned up.
2. **Scan:** list `raw_root` using the `include` and `exclude` patterns. Every file is
   tracked in `rig_files` with its size and mtime. New files are `seen`. Files that
   disappear before collection are `gone`: if a gone file was never collected, raise
   `DATA_AT_RISK` (it may have been deleted on the rig before any copy existed).
3. **Stability:** a file is collectable once its size and mtime have not changed for
   `stable_seconds` (across polls), **and** it can be opened over SMB with **exclusive
   share mode**, which fails while NINA still has it open for writing. It must also parse
   as FITS or XISF with a data size that matches the header (catches truncated files).
4. **Copy and hash:** stream the file from the rig into `cache\tmp\`, computing SHA-256
   and parsing the header while copying.
5. **Verify with a double read** (`verify: double_read`, the default): read the file on
   the rig a **second time** and compute SHA-256 again. The two hashes must match (and
   the size and mtime must be unchanged). A mismatch means a transient read error or a
   file still changing: discard the copy and retry on the next poll. After 3 failures,
   raise `COLLECTION_CORRUPT`. This doubles LAN reads (about 10 GB per 100-frame night),
   which is cheap on wired Ethernet, in exchange for knowing that the hash that protects
   every later copy really matches the rig's file. `single_read` skips the second read.
6. **Commit:** atomically rename the copy into `cache\blobs\…` (read-only). Record the
   blob and both replicas (`rig:<name>` and `cache`) and hand the frame to ingest (§6.2).
   Ingest puts a `raw_light` **at the front of the backup queue** immediately.
7. **Throttle:** at most `parallel_files` at once, with `bandwidth_limit_mbps` if set. With
   `during_capture: false`, steps 4–6 wait until the night's session-end marker or
   quiescence. That setting is not recommended, because backup then starts only after
   the session.

**Closing a night:** when the session-end marker appears (or quiescence, or the scheduled
fallback, §6.1), the collector does a final full scan and collects everything remaining
for that night. It then writes the **collection manifest**
`raw/<rig>/_manifests/<night>.json`, a `metadata` blob that is backed up right away:

```json
{
  "schema": 1,
  "rig": "esprit100_2600mm",
  "raw_root": "//rig-esprit/NINA",
  "night": "2026-09-24",
  "closed_by": "session_end_marker",
  "collector_version": "0.5.0",
  "files": [
    {
      "logical_path": "raw/esprit100_2600mm/2026-09-24/M31/LIGHT/Ha/2026-09-24_23-10-02_Ha_300.00s_0001.fits",
      "rig_path": "2026-09-24/M31/LIGHT/Ha/2026-09-24_23-10-02_Ha_300.00s_0001.fits",
      "sha256": "9f2c…",
      "size": 52436160,
      "class": "raw_light",
      "headers": { "IMAGETYP": "LIGHT", "FILTER": "Ha", "ROTATOR": 31250, "…": "…" }
    }
  ]
}
```

Because the manifest carries the headers, the catalog can be rebuilt from manifests
without downloading any image (§7.9).

**Frames that arrive late** (after the night was closed, for example NINA flushing a
buffered frame) are still collected and backed up. They re-open the night, which triggers
a re-plan and an updated manifest (a new version; the old one is kept).

**What the processing PC writes on a rig PC:** nothing, except deletions made by cleanup
(§7.6). The `_altair\` folder belongs to the NINA session-end script. The collector only
reads it, apart from cleaning up old markers.

### 7.4 Data classes & backup policy

Every blob has a **logical path**. File-system locations store it at
`<root>\<logical path>`, and the S3 key is `<prefix><logical path>`. Using the same tree
everywhere keeps every location browsable by hand and makes rebuild-from-archive possible.

| Data class | What | Logical path | S3 | NFS (if enabled) | Storage class → lifecycle |
|---|---|---|---|---|---|
| `raw_light` | Raw light subs from NINA | `raw/<rig>/<night>/<NINA relative path>` | **Yes — first, during the night** | Yes | STANDARD_IA → DEEP_ARCHIVE after 120 d |
| `raw_calibration` | Raw dark / flat / bias / dark-flat subs | `raw/<rig>/<night>/…` | **No** | No by default (can be enabled; NFS space is cheap) | — |
| `calibration_master` | Master dark / flat / bias / dark-flat (+ sidecar) | `calibration/masters/<kind>/…/<name>_<sha8>.xisf` | Yes | Yes | STANDARD |
| `project_reference` | Project reference frame (+ WCS sidecar) | `projects/<project>/reference/reference_v<N>.xisf` | Yes | Yes | STANDARD |
| `calibrated_frame` | Calibrated light subs from a **final** (flat-verified) night stack | `projects/<project>/nights/<night>/<filter>/calibrated/<name>_c.xisf` | Yes | Yes | STANDARD_IA → GLACIER_IR after 60 d |
| `night_master` | Night master (+ `night.json`) | `projects/<project>/nights/<night>/<filter>/night_master_<sha8>.xisf` | Yes | Yes | STANDARD |
| `multi_night_master` | Multi-night master versions (+ sidecar, coverage map) | `projects/<project>/multinight/<filter>/v<NNN>.xisf` | Yes | Yes | STANDARD |
| `metadata` | Manifests, sidecars, reports, catalog backups | `raw/<rig>/_manifests/…`, `catalog/…` | Yes | Yes | STANDARD |
| `registered_frame` | Registered subs | `projects/…/registered/…` (cache only) | **No** | **No** | — (local only) |
| `provisional` | No-flat preview masters and their calibrated subs | cache and published copy only | **No** | **No** | — |

Why this split:

- **Raw lights** can never be recreated, so they are backed up first. Missing-flat reruns
  usually happen within weeks, while the raw lights are still in STANDARD_IA and can be
  read straight away. After 120 days they move to Deep Archive, which is cheap and only
  needed for disaster recovery or reprocessing from scratch.
- **Raw calibration subs are not backed up.** Their masters are. So a calibration sub is
  only cleaned up after the master built from it has a verified backup (§7.6). The
  trade-off: a master flat or dark can't be rebuilt from its subs later (for example with
  different rejection settings), but you can always reshoot calibration frames.
- **Calibrated light subs** let a re-reference (§9.7) or a frame-level re-integration
  (§9.6) start from already-calibrated data, without the raw calibration subs and without
  restoring raw lights from Deep Archive. They move to Glacier Instant Retrieval, so they
  can always be read immediately. `calibrated_frame_stage` chooses which WBPP intermediate
  is kept: `calibrated` (the default; pre-cosmetic, pre-debayer) or a later stage. They
  are uploaded with XISF lossless compression (`zstd+shuffle`). Only subs from a **final**
  night stack are backed up, never from a provisional no-flat run.
- **Registered subs** are not backed up. They exist only in the cache while needed and can
  be regenerated from calibrated subs plus the project reference (§9.6).

**Volume estimate** (per rig-night, 26 MP mono, 100 lights): raw lights ≈ 5.2 GB (16-bit).
Calibrated subs ≈ 10.4 GB before compression (32-bit float; compressed size to be measured
in Phase 1). Masters ≈ 0.2 GB. Calibrated subs are the biggest item. If S3 cost or uplink
time becomes a problem, per-project `backup_calibrated_frames: false` turns them off. At
20 Mbps upload, about 16 GB takes about 1.8 h. `altair storage status` shows the current
backlog, growth per month, and a rough monthly cost estimate per storage class, using
prices you enter in config.

### 7.5 Backup engine (S3 and NFS)

A **replicator** worker in `altaird` uploads `(blob, location)` pairs according to
`backup_classes`.

**Priority order:** `raw_light` (pre-empts everything, ignores `upload_window`) →
`metadata` → `calibration_master` / `project_reference` / `night_master` /
`multi_night_master` → `calibrated_frame`.

**S3 upload:**

- **Upload:** multipart upload with `ChecksumAlgorithm=SHA256`, so S3 verifies every part
  on arrival. The full-file SHA-256 is also stored as object metadata
  `x-amz-meta-altair-sha256`, because multipart checksums are composite. The storage
  class comes from `storage_class`.
- **Never overwrite:** uploads use a **conditional write** (`If-None-Match: *`), so an
  existing key is never overwritten. If the key already exists, its stored SHA-256 is
  compared. A match means "already backed up". A mismatch raises `INTEGRITY_MISMATCH` and
  is never overwritten.
- **Confirm:** `HeadObject` (with `ChecksumMode=ENABLED`) confirms the object and its
  metadata. Only then is the replica marked `present`, verified `s3_checksum_sha256`.
- **Resumable:** interrupted multipart uploads resume by upload ID. A bucket lifecycle rule
  aborts orphaned multipart uploads after 7 days.

**NFS copy:** copy to a temp file, read it back and re-hash it, then rename. An existing
file with the same hash counts as already backed up. A different hash raises
`INTEGRITY_MISMATCH` and is never overwritten.

**Bucket setup (`altair storage s3 init`, run once with an admin profile):**

- **Versioning** on. Even a mistaken overwrite or delete is recoverable.
- **Default encryption** (SSE-S3 or SSE-KMS). **Block Public Access** on.
- **Lifecycle rules** from `lifecycle`. These only **transition** storage classes and abort
  incomplete multipart uploads. They never expire backed-up data.
- *Optional:* **Object Lock** (governance mode) on `raw/`, with a retention period, for
  protection against ransomware and against accidents.
- It creates the least-privilege **IAM policy** that the daemon's credentials use. **It
  gives no permission to delete anything under `raw/`, `calibration/`, or `projects/`
  except superseded multi-night versions and old catalog backups.** So "no deletions
  synced to S3" is enforced by AWS, not only by Altair's code:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketVersioning", "s3:GetLifecycleConfiguration"],
      "Resource": "arn:aws:s3:::my-astro-archive" },
    { "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:GetObjectAttributes", "s3:RestoreObject",
                 "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
      "Resource": "arn:aws:s3:::my-astro-archive/altair/*" },
    { "Effect": "Allow",
      "Action": "s3:DeleteObject",
      "Resource": ["arn:aws:s3:::my-astro-archive/altair/projects/*/multinight/*",
                   "arn:aws:s3:::my-astro-archive/altair/catalog/*"] }
  ]
}
```

`altair doctor` checks versioning, encryption, public-access block, lifecycle rules, and
that the daemon's credentials **cannot** delete under `raw/`. It checks the last one with
IAM's policy simulator, or by trying a delete of a dedicated canary key and confirming it
is refused.

**Bandwidth:** `upload_bandwidth_limit_mbps` and `upload_window` apply to everything except
`raw_light`.

### 7.6 Retention & cleanup

Cleanup is the **only** code in Altair that deletes files. Every rule is **per location**.
A deletion in one location never causes one in another, and the backup copy is always
re-checked right before a delete.

| Location | Class | Deleted when (all must hold) |
|---|---|---|
| **Rig PC** (each rig's NINA folder, via its `raw_root`) | `raw_light` | collected and verified (double read) · verified backup in S3 (or the NFS if S3 is not configured) · the file on the rig still has the collected size, mtime, and SHA-256 · older than 7 days |
| | `raw_calibration` | collected and verified · the master(s) built from it have a verified backup · unchanged on the rig · older than 7 days |
| | anything | also cleaned oldest-first before the age limit **only** while the rig's free space is below `target_free_percent`, still subject to every other condition |
| | session-end markers | older than `markers_keep_days` |
| **Cache** (processing PC) | raw frames | only after `pin_raw_until` holds: verified backup · night processed · **not tied to an open issue**, so a flat-blocked night's lights stay local for a fast rerun |
| | any other | LRU eviction (§7.7). Never pinned blobs, inputs of queued jobs, or blobs whose only verified copy is the cache |
| **Work** dirs | — | right away after a successful job. Failed jobs are kept 7 days for debugging |
| **Published** viewing copies | — | night viewing copies after `night_masters_keep_days`. Multi-night viewing copies beyond the latest `multi_night_versions_keep` |
| **Logs** | — | after `logs_keep_days` |
| **NFS** archive | — | never, unless you add rules |
| **S3** archive | kept classes | **never**. The IAM policy doesn't allow it (§7.5) |
| | `multi_night_master` | versions beyond the newest `multi_night_master_versions_keep`. They can be regenerated from the backed-up night masters |
| | `metadata` (catalog backups) | beyond 30 daily + 12 monthly |

**How a cleanup run works:**

1. **Plan:** pick candidates by rule and write the plan to the cleanup ledger.
   `altair storage cleanup --dry-run` prints it, with the space it would free.
2. **Re-check:** for each candidate, confirm the required backup again (a fresh S3
   `HeadObject` with checksum, or a file-system `stat` plus the stored hash) and confirm
   the blob still has at least one other verified copy.
3. **Delete** that one copy and mark its replica `missing` (reason `cleanup`). The ledger
   records every deletion (what, where, why, and which backup it relied on).
4. On rig PCs, remove folders under `raw_root` that are now empty. Never remove
   `raw_root` itself, `_altair\`, or any folder NINA is currently writing to.

**Rig PC cleanup runs from the processing PC** over the rig's share. It needs the share's
**Modify** permission (§4.1). If the rig is unreachable, its cleanup simply waits. With
`cleanup.enabled: false` for a rig, the dry-run report lists what is safe to delete, and
you delete it by hand.

**Deletions outside Altair** (you, a disk failure on a rig, or someone tidying the NINA
folder): the next scan marks those replicas `missing`. A frame deleted on a rig **before it
was collected** raises `DATA_AT_RISK` immediately (§7.3). If a blob now has no verified
backup, `DATA_AT_RISK` is raised as well. Nothing is ever deleted in response.

### 7.7 Staging: fetch on demand

Before any job runs, the **stager** gets every input blob into the local cache:

1. **Cache hit:** the blob is `present` in the cache → pin it for the job.
2. **Choose a source:** reachable locations holding a `present` replica, in
   `read_priority` order: cache → `rig:<name>` (the rig PC's original, while it still
   exists) → NFS → S3 (instant classes: STANDARD, STANDARD_IA, GLACIER_IR) → S3 cold
   (DEEP_ARCHIVE, GLACIER). Replicas marked `corrupt` are skipped.
3. **Cost and size guards:** if the bytes to download from S3 exceed
   `max_auto_download_gb`, raise `FETCH_APPROVAL_NEEDED`. It shows the size, the estimated
   egress cost, and the reason (for example "re-reference M31: 412 calibrated subs,
   38 GB"). The job waits (`waiting_data`) until `altair storage approve <issue>`.
4. **Cold objects** (raw lights older than 120 days are in Deep Archive):
   - One `RestoreObject` request goes out per needed blob (tier and days from config),
     batched across the whole job. The job is `waiting_data`, and an info issue
     `RESTORE_IN_PROGRESS` shows the tier and a rough ETA. For Deep Archive, Bulk is
     typically ≤48 h and Standard ≤12 h.
   - A poller checks `HeadObject` (`x-amz-restore`) every 30 min, and downloads each blob
     as its restore completes.
   - If the total restore exceeds `max_auto_restore_gb`, a `RESTORE_APPROVAL_NEEDED` issue
     is raised before anything is requested.
   - Where calibrated subs can do the job instead (re-reference, frame re-integration), the
     planner uses them. They are in instant-retrieval classes, so no restore is needed.
5. **Download and verify:** parallel, resumable (HTTP range) downloads to
   `cache\tmp\`, with a streaming SHA-256 check. A mismatch marks that source replica
   `corrupt`, raises `INTEGRITY_MISMATCH`, and tries the next source. On success the file
   is atomically renamed into `cache\blobs\<aa>\<sha256>.<ext>` and marked read-only.
6. **No source available:** raise `DATA_UNAVAILABLE` (blocking). It lists the blobs and
   their last known locations (for example "only on rig:esprit100_2600mm, which is
   unreachable"). The job stays in `waiting_data` and re-queues automatically when any
   location becomes reachable again (locations are probed every 10 min) or when a replica
   turns up.
7. **Hand-off:** inputs are **hard-linked** (NTFS, same volume) into
   `work\<job-id>\inputs\` under their original file names. This copies no data and keeps
   WBPP logs readable.
8. **Prefetch:** staging starts as soon as a job is planned, even while other jobs occupy
   PixInsight. Restores for reruns and re-references are requested right away.

**Cache eviction:** least-recently-used eviction of unpinned blobs, down to
`max_size_gb` and `min_free_gb`. Never evicted: inputs of queued or running jobs, pinned
data classes, and any blob whose **only verified copy is the cache** (for example, a new
night master not yet uploaded). If the cache can't make room, `CACHE_FULL` is raised.
Registered subs are evicted as soon as the job that needs them finishes.

### 7.8 Integrity scrubbing

Every `scrub_interval_days`, a random `scrub_sample_percent` of backed-up replicas in S3
and on the NFS is re-verified. For S3 that means a checksum `HeadObject`, plus a full
download for 10% of the sample, **skipping cold classes** so no restore is triggered. For
the NFS it means a full re-hash. A corrupt replica is re-uploaded from a good copy under
the same key. S3 versioning keeps the bad version for inspection; this is the one case
where a key's content is written again, and only with the matching SHA-256.
`INTEGRITY_MISMATCH` is raised.

### 7.9 Catalog backup & disaster recovery

- **Nightly catalog backup:** SQLite online backup → zstd → uploaded to
  `catalog/altair-<utc>.db.zst` in S3 (and on the NFS). Kept: 30 daily and 12 monthly.
- **Self-describing archive:** manifests (with headers), calibration master sidecars,
  `night.json`, and multi-night sidecars are all stored next to the data under the same
  logical paths. If every catalog backup were lost,
  `altair storage rebuild-catalog --from s3` would rebuild everything from a bucket
  listing, the manifests, and the sidecars, **without downloading or restoring any
  images**: blobs, replicas, frames, calibration masters, projects, night masters, and
  multi-night history. Issues are not restored; they are re-derived by a re-plan. Raw
  calibration subs are not in the archive, so they are not in a rebuilt catalog either.
  Their masters are.
- **Losing the processing PC:** install Altair on the new PC, restore `altair.yaml`, run
  `altair storage restore-catalog --latest`, and start. The cache starts empty, and
  everything is fetched on demand as jobs need it.
- The disaster-recovery drill is an exit criterion (§15).

### 7.10 Adding the NFS later

1. Mount or share it, set `enabled: true` on the `nfs` location, and choose its
   `backup_classes` (the default is the same as S3; adding `raw_calibration` is
   reasonable).
2. `altair storage replicate --to nfs --dry-run` shows how many bytes are still on the rig
   PCs or in the cache (free to copy), and how many would have to come from S3 (egress cost,
   and restores for Deep Archive objects). Run it without `--dry-run` to backfill, or with
   `--from-local-only` to skip anything that needs S3.
3. With `read_priority` 20 (ahead of S3), reruns and re-references are then served from
   the NFS, and S3 becomes purely an offsite backup.
4. Cleanup rules don't change. A backup on either the NFS or S3 satisfies "verified
   backup", except for raw lights on the rig PCs, which need S3 whenever S3 is configured.
   That way an offsite copy exists before the rig's original is removed.

No catalog migration is needed. It only adds a location and replicas.

### 7.11 Physical layout

```
Rig PC (one per telescope) — location "rig:<name>", reached as rigs.<name>.raw_root
D:\NINA\                                       # NINA save folder, shared as \\<host>\NINA
├── 2026-09-24\M31\LIGHT\Ha\...fits            # NINA's own layout (file pattern §4.2)
└── _altair\session-end-<timestamp>.json        # written by the NINA end-of-sequence script
C:\Tools\altair-session-end.cmd                # the only Altair file on the rig PC

s3://my-astro-archive/altair/                  # location "s3" — same logical tree
├── raw/<rig>/<night>/...                      # raw lights + collection manifests (no calibration subs)
├── calibration/masters/...
├── projects/<project>/{reference, nights/<night>/<filter>/{calibrated, night_master_*}, multinight}/
└── catalog/

Processing PC
E:\AltairCache\                                # location "cache"
│   ├── blobs\<aa>\<sha256>.<ext>              # content-addressed, read-only
│   └── tmp\                                   # in-flight downloads
E:\AltairWork\<job-id>\inputs\                 # hard links into the cache (same volume)
C:\ProgramData\Altair\{altair.yaml, state\altair.db, state\logs\, state\cleanup-ledger\}
D:\Astro\Masters\                              # published viewing copies + ALTAIR_STATUS.html
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
- **Storage:** registered subs are never backed up (§7.4). What is backed up is each final
  night's **calibrated light subs**. A frame re-integration stages those (they are always
  in instant-retrieval storage classes) and runs `StarAlignment` against the **same**
  reference version to rebuild the registered subs in the cache. The geometry is the same
  as the original night stack, so it is a regeneration step rather than a new
  registration choice, and the result is evicted once the merge finishes. If you run this
  mode often, set `cache.pin` to include `registered_frame` for that project to avoid
  regenerating them each time.

### 9.7 Re-reference (explicit only)

`altair project rereference <project> [--from-night N]` picks a new reference (for
example, the first night was poor). It increments `reference_version`. All night masters
become `STALE_REFERENCE` and are reprocessed from the backed-up **calibrated light subs**
(so no raw calibration subs or Deep Archive restores are needed). The stager fetches them
from wherever they now live (§7.7). The command first prints the total input size and
where it will come from (for example "412 calibrated subs, 31 GB: 6 GB from the cache,
25 GB from S3 Glacier IR"). The fetch goes through the normal approval guards. This is the only operation that repeats registration for existing nights, and it
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
| `RIG_UNREACHABLE` | warning, then blocking after 12 h | collection, and cleanup on that rig | the rig's `raw_root` is reachable again |
| `COLLECTION_STUCK` | blocking | closing that rig-night | the file becomes stable and readable, or you exclude it (`altair collect exclude`) |
| `COLLECTION_CORRUPT` | blocking | those files | a later double read matches |
| `SESSION_END_MARKER_MISSING` | warning | nothing (the night closed by quiescence) | — (informational; check the NINA end-of-sequence script) |
| `MANIFEST_MISSING` | warning | nothing (the night is processed from a rescan) | a manifest arrives |
| `LOCATION_UNREACHABLE` | warning | fetches from that location | the probe succeeds |
| `DATA_UNAVAILABLE` | blocking | jobs needing those blobs | a replica becomes reachable or is discovered |
| `RESTORE_IN_PROGRESS` | info | jobs needing those blobs (they wait) | restore completes and the download verifies |
| `FETCH_APPROVAL_NEEDED` / `RESTORE_APPROVAL_NEEDED` | blocking | that job | `altair storage approve <id>` (or `deny`, which cancels the job) |
| `INTEGRITY_MISMATCH` | warning | nothing (another copy is used) | the bad replica is re-replicated and verified |
| `BACKUP_BEHIND` | warning | cleanup of that night | raw lights reach a verified S3 copy |
| `DATA_AT_RISK` | blocking (alerts immediately) | all cleanup of the affected blobs | a verified backup exists |
| `S3_CONFIG_UNSAFE` | blocking | cleanup (all locations) | `doctor` finds versioning on and delete denied under `raw/` |
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
before any equipment change. The collector picks them up from the rig as usual.
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
 issue opened ──► user takes flats in NINA ──► collector pulls + verifies ──► ingest
      ▲                                                                   │
      │                                  CALIB_MASTER builds master flat ◄┘
      │                                                  │
      │               planner: does any open issue's requirement_json match?
      │                           │ yes
      │                           ▼
      │      NIGHT_STACK rerun for blocked night(s) (stager fetches raw lights
      │      from cache / rig PC / NFS / S3, restoring from Glacier if needed)
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
- **Raw data for reruns:** held lights stay linked to their issue and stay pinned in the
  processing PC's cache while the issue is open (`pin_raw_until`). If they are gone anyway
  (for example, the cache was wiped), the stager fetches them from the rig PC if it still
  has them, then from the NFS or S3. For the first 120 days
  they are readable immediately (STANDARD_IA). After that a Deep Archive restore is
  needed, and the issue's status line shows "waiting for restore, ETA …". The rerun is
  only slower, never impossible.
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
- **Raw data is never lost.** Raw lights are backed up to S3 (and the NFS) as they land.
  Raw files are never moved or modified. A copy is deleted only by cleanup (§7.6), only
  after the backup is re-verified, and deletions never propagate between locations (S3
  delete permission on `raw/` does not exist).
- **Resumable transfers:** collection copies, uploads, downloads, and restores are all
  identified by blob hash and are idempotent. After a crash they resume (S3 multipart
  resume, HTTP range) or restart cleanly. Nothing half-transferred is ever marked
  `present`.
- **Storage workers are independent of PixInsight:** replication, staging, restore
  polling, cleanup, and scrubbing run in their own worker pool with their own
  concurrency limits. A multi-hour Glacier restore or a slow upload never blocks
  processing of data that is already available.

---

## 12. Interfaces

### 12.1 CLI (`altair`)

```
altair serve [--windowless]
altair rigs list | check [--rig R]                # reachability, credentials, share permissions, free space per rig
altair collect status [--rig R]                    # open nights, uncollected/stuck files, last poll
altair collect now --rig R | close-night --rig R --night DATE | exclude --rig R <rel-path>
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
altair storage backup status [--night DATE]       # backup progress per class and location; BACKUP_BEHIND items
altair storage replicate --to LOC [--classes ...] [--dry-run] [--from-local-only]   # backfill, e.g. after adding NFS
altair storage cleanup [--dry-run] [--location LOC]   # retention rules per §7.6; prints space freed
altair storage ledger [--since DATE]              # every deletion and the backup it relied on
altair storage s3 init | apply-lifecycle | check  # bucket setup, lifecycle, IAM policy (admin profile)
altair storage scrub [--location LOC] [--sample PCT]
altair storage backup-catalog | restore-catalog [--latest|--at TIME] | rebuild-catalog --from LOC
altair publish --refresh
altair doctor                                     # PixInsight/CLI flags, session type, paths, disk, NINA headers sample,
                                                  # rig shares (read/modify), S3 access, bucket versioning/encryption, S3 delete-denied check
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
| **1: Collector, ingest & backup** *(ships first: protects raw data before any processing exists)* | Per-rig config and **collector** (§7.3): SMB polling of each rig's `raw_root`, stability and exclusive-open check, double-read SHA-256 verification, collection manifests, session-end marker and quiescence handling, `RIG_UNREACHABLE`. The NINA session-end script. Ingest (config, header mapping, rig and rotator extraction, blob/replica/ledger schema). **Backup engine** (§7.5): raw-first S3 upload with SHA-256 checksums and conditional writes, NFS copy, output classes wired for later. `altair storage s3 init` (versioning, encryption, lifecycle, least-privilege IAM). **Cleanup** (§7.6) of rig PC NINA folders over SMB and of the cache, with ledger and dry-run. Catalog backup. `altair storage backup status`. | Two rigs' real nights are collected during the night and in S3 (and on the NFS if present) within the backup time limit, verified by hash. Unplugging a rig's network mid-night loses nothing, and collection catches up after reconnecting. `doctor` proves the daemon **cannot** delete under `raw/`. Property tests: cleanup never deletes the last verified copy or an uncollected rig file, and deleting a copy in one location never deletes one elsewhere. Rig PC disk space is reclaimed on schedule. |
| **2: Planner & matching** | §8 rules, including rotator and equipment events, issue generation, `altair plan`. | Table-driven tests for every rule: rotator Δ at tolerance ±1 step, wrap-around, missing position, event between flat and light. |
| **2b: Staging & retrieval** | §7.7: stager (source selection, verify, hard-link hand-off, cache eviction), Deep Archive restore flow, approval guards, scrub, `restore-catalog` / `rebuild-catalog`. | Against MinIO / S3: wipe the cache and delete the rig originals → a job still runs from S3. Deep Archive objects → restore → run. A corrupted replica is detected and bypassed. `rebuild-catalog` from the bucket alone reproduces the catalog. |
| **3: Night stacks** | Executor (Job Objects, timeouts), `PROJECT_REFERENCE`, `NIGHT_STACK`, verifier, publisher, output backup (masters, reference, calibrated subs). | A night master matches a manual WBPP run on the same data within noise, and is pixel-aligned with the reference. |
| **4: Calibration library** | `CALIB_MASTER`, rotator-tagged master flats, import, supersession. | A flats-only night produces masters that are matched automatically. |
| **5: Merger** | §9: gates, measured weighting, LN, coverage-aware integration, versions, reports. | A 3-night merge's weights match the measured PSF signal ordering. Excluding a night and re-including it reproduces the same result byte for byte. Uncovered edges don't darken the result. |
| **6: Issues & rerun loop** | §10: issue store, dedup, toast, push, email, status page, auto-rerun on calibration arrival, manual resolve and waive. | End to end: a night without SII flats → issue + toast → take matching flats in NINA → collector pulls them → automatic rerun (with raw lights fetched from S3 if the cache and rig copies are gone) → merge → "resolved" notification, with no CLI use. |
| **7: Daemon & hardening** | Triggers (session-end markers, quiescence, scheduled fallback), Task Scheduler install script (processing PC), rig PC setup guide (share, permissions, session-end script), sleep prevention, crash recovery, `altair doctor`. | A week of unattended real nights from two rigs. Survives killing `altaird` and `PixInsight.exe` mid-job, and a rig PC rebooting mid-night. |
| **8: Disaster-recovery drill** | Documented runbook. | On a clean machine, with only `altair.yaml` and S3: `restore-catalog`, then produce a multi-night master update for one project. |

Phase 1 does not depend on the PixInsight spike (Phase 0). It is built first, or in
parallel, so the raw-data backup is running on real nights before any processing code
exists.

### 15.1 Proposed source layout

```
altair-pre-processor/
├── pyproject.toml
├── altair.example.yaml
├── src/altair/
│   ├── cli.py  config.py  daemon.py
│   ├── collector/    # rig_worker.py, scan.py, stability.py, copy_verify.py, manifest.py, session_end.py
│   ├── triggers/     # schedule.py, dawn.py
│   ├── ingest/       # headers.py, nina.py, rotator.py, normalize.py, classify.py
│   ├── storage/      # blobs.py, locations/{fs.py, s3.py}, replicator.py, stager.py, restore.py,
│   │                 # cache.py, cleanup.py, s3_setup.py, scrub.py, catalog_backup.py, rebuild.py
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
│   ├── altair-session-end.cmd    # the NINA end-of-sequence script (copied to each rig PC)
│   ├── rig-setup.md              # share the NINA folder, share account, permissions
│   └── nina-external-script.md
├── tests/
│   ├── fixtures/                 # synthetic NINA-style FITS (astropy), tiny images
│   ├── test_rotator.py  test_matching.py  test_gates.py  test_issues.py
│   ├── test_weights.py           # weighting math on synthetic noise/signal
│   ├── test_storage_*.py         # backup, stager, restore, cleanup invariants (moto + MinIO)
│   ├── test_collector.py         # stability, partial writes, locked files, rig outage, double-read mismatch, late frames
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
  truncated downloads, flipped bytes, unreachable locations, files deleted on a rig before and after collection, and
  restores that never finish. **Property tests** on cleanup assert that no sequence of
  operations ever leaves a `raw_light` (or any backed-up class) with zero verified copies,
  and that deleting a copy in one location never removes a copy in another.
- **Contract:** a fake `PixInsight.exe` (a small Python-built exe or `.cmd` shim) that
  validates `job.json` and emits canned outputs. This runs in CI on `windows-latest`.
- **Integration (local, marked):** real PixInsight on a small reference dataset of 2
  nights, 2 filters, and one rotator change, with one night deliberately missing flats.
  Golden checks are statistical (median, MAD, star count, weight ordering), not
  byte-for-byte.
- **Soak:** replay a recorded NINA night's file arrivals into a rig PC's NINA folder,
  with the collector pulling over a real SMB share from a second machine.

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
| R7 | A bug or a mistaken command in Altair deletes archived data. | The daemon's IAM policy has no delete permission on kept prefixes. Bucket versioning. Conditional writes (no overwrites). Optional Object Lock on `raw/`. Cleanup re-verifies the backup before every delete, and deletions are recorded in a ledger. Property tests. |
| R11 | Raw calibration subs are not backed up. | Their masters are, and subs are only cleaned up after the masters' backup is verified. Masters can't be rebuilt from subs with new settings later. The NFS can be set to keep subs. |
| R12 | Calibrated subs roughly triple S3 volume (32-bit float). | Compression, a Glacier IR transition after 60 days, and a per-project opt-out. `storage status` shows growth and a cost estimate. |
| R8 | Glacier restore latency (hours) and retrieval or egress costs on reruns and re-references. | Prefetch as soon as a job is planned. Approval guards with cost estimates. `pin_raw_until` keeps likely rerun inputs in the processing PC cache. The NFS (if added) serves reruns first. |
| R9 | Losing the catalog makes the archive hard to navigate. | Nightly catalog backups to every durable location, plus the self-describing archive (manifests with headers, sidecars) and `rebuild-catalog`. |
| R10 | SMB read errors, or partially written frames. | Stability plus exclusive-open check, FITS size validation, double-read SHA-256, temp file + rename, and hash checks on every later fetch. |
| R13 | A rig's frames exist only on that rig until collected. | Collection during the night (about a minute behind capture). `RIG_UNREACHABLE` escalation. Nothing uncollected is ever cleaned up. `DATA_AT_RISK` if an uncollected file disappears. |
| Q1 | Is the rotator reported in degrees or steps, and is steps-per-revolution known? Which rotator and driver? | Sets the rig's `rotator` defaults. |
| Q2 | ~~Same PC or separate?~~ **Decided:** one mini PC per rig, each sharing its own NINA folder, and a separate processing PC with a config entry per rig. | §3.0, §4.1, §5. |
| Q5 | ~~S3 provider?~~ **Decided:** Amazon S3, with Altair doing all uploads and never syncing deletions. Proposed defaults: raw lights STANDARD_IA → DEEP_ARCHIVE at 120 days; calibrated subs STANDARD_IA → GLACIER_IR at 60 days; masters STANDARD. Are those day counts right for how long you usually wait before shooting missing flats or reprocessing? | Sets `storage_class` and `lifecycle`. |
| Q6 | ~~External backup tool?~~ **Decided:** none. Altair owns the backup. | §7.5. |
| Q7 | NFS: add it? If so, should it also keep raw calibration subs (not kept in S3)? | Sets `nfs.backup_classes`. |
| Q9 | Which calibrated stage to keep: pure calibrated (default), cosmetic-corrected, or debayered (OSC)? | Sets `calibrated_frame_stage`. |
| Q8 | Network link from each rig PC to the processing PC (wired or Wi-Fi, speed), and the internet uplink and downlink? | Sizes staging expectations, upload windows, and restore-to-run ETAs. |
| Q3 | Should the multi-night master require every night to cover the full frame (strict crop), or allow partial-coverage edges? | Default: crop to full coverage (`min_coverage_nights: all`). |
| Q4 | Default weighting: `measured_psf_signal` or `inverse_noise_variance`? | Spec default is PSF signal (it rewards seeing and transparency too). Phase 5 compares both on real data. |
