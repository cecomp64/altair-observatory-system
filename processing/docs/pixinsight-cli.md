# PixInsight runner contract (SPEC §6.5)

Altair runs PixInsight once per job:

```
PixInsight.exe -n=<instance_slot> --automation-mode --no-startup-scripts --force-exit ^
    -r=<pjsr>\altair_runner.js,<work>\<job-id>\job.json
```

- `src/altair/executor/pixinsight.py` builds the command line and starts the process:
  - without a window, at `pixinsight.priority`;
  - inside a Windows Job Object that kills the whole process tree on a timeout
    (`timeout_minutes`).
- Console output goes to `state\logs\jobs\<job-id>.log`.
- `src/altair/pjsr/` holds the runner. It ships inside the Altair package, and
  `pixinsight.runner` overrides where it is loaded from.
- The runner reads `job.json` from `jsArguments[0]` and dispatches on `kind`, and on
  `phase` for `MERGE`.
- The runner **always** writes `result.json` to `work_dir`, from its catch block
  too. A missing `result.json`, `status: "error"`, a non-zero exit code or a timeout
  fails the run. The run is then retried with back-off up to `max_attempts`, after
  which `JOB_FAILED` is raised.

## job.json

Common fields:

| Field | |
|---|---|
| `schema` | `1` |
| `job_id`, `kind`, `plan_hash` | the catalog's job |
| `work_dir` | scratch for this run, on local NVMe; every intermediate goes here |
| `output_dir` | `work_dir/out`: where the outputs listed in result.json are written |
| `pixinsight.wbpp_dir`, `pixinsight.engine` | `native` or `wbpp` (see below) |

Every input is given as `{sha256, path}`:
- Raw lights and subs are NAS paths, read in place.
- Masters and references are cache files hard-linked into `work_dir/inputs/`.

### Fields by kind

- **`CALIB_MASTER`**
  - `master`: kind, camera, gain, offset, binning, exposure, sensor_temp, filter,
    focal_length, rotator_pos, rotator_units, night, and the keywords to stamp.
  - `frames`: the subs.
  - `calibrate_with`: for a FLAT, `{darkflat | bias: {sha256, path}}`.
- **`PROJECT_REFERENCE`**
  - `project_id`, `night`, `version`.
  - `filters`: in order of preference.
  - `groups[]`: `{filter, exposure, rotator_pos, lights[], dark, flat}`.
  - `camera.type`: `mono` or `osc`.
- **`NIGHT_STACK`**
  - The same fields as `PROJECT_REFERENCE`, plus `filter`, `stack_kind`
    (`final` / `provisional_noflat`), `drizzle_scale`, `keep_calibrated_frames`,
    `reference_version` and `wbpp_profile`.
  - `reference`: the project reference, `{sha256, path}`.
  - `grouping_keywords`.
  - Register to `reference` with autocrop **off**: the master stays in project
    geometry, and uncovered pixels are 0.
- **`MERGE`**
  - `phase` is `measure` or `integrate`.
  - `project_id`, `filter`, `mode`, `weighting`, `normalization`, `rejection`,
    `min_coverage_nights`, `autocrop`, `reference`.
  - `nights[]`: `{night, sha256, frames, exposure_s, path}`.
  - `calibrated_frames[]`: for `frame_reintegration`.
  - For `integrate` only: `weights` (`{sha256: weight}`, computed by Altair from the
    measure phase) and `normalization_reference`.

## result.json

```json
{
  "status": "ok",
  "error": null,
  "outputs": [{"role": "master", "path": "…/out/night_master.xisf", "source_sha256": null}],
  "metrics": {"frames": 42, "rejected": 3, "fwhm": 2.6, "eccentricity": 0.44, "overlap_fraction": 0.97},
  "frames": [{"sha256": "…", "used": true, "fwhm": 2.5, "eccentricity": 0.43, "stars": 812, "psf_signal_weight": 0.021, "weight": 0.021, "reason": null}],
  "measurements": [],
  "software": {"pixinsight": "1.9.3", "runner": "1"}
}
```

### Output roles

| Role | Produced by |
|---|---|
| `master` | every kind |
| `reference` | `PROJECT_REFERENCE`; `source_sha256` is the light it came from |
| `calibrated_frame` | `NIGHT_STACK`, final stacks only; `source_sha256` is the light it came from |
| `coverage` | `MERGE` |
| `rejection_low`, `rejection_high` | `NIGHT_STACK`, `MERGE` |
| `viewing` | optional: an autocropped copy for `paths.published` |
| `preview` | optional: a small file to render the Hub preview from |

### The measure phase

`MERGE`'s measure phase returns `measurements[]`:
`{sha256, psf_signal_weight, noise_sigma, scale, fwhm}` for every night master, all
measured in one pass.

## After the run

`src/altair/publish/publisher.py`:
- checks the night stack (§6.6 step 1);
- stores every output as a blob under a logical path with a version or hash suffix
  (NAS write verified, a cache copy, S3 via the replicator);
- records the master row;
- updates frame statuses;
- writes the viewing copy;
- reports to the Hub.

A crash while publishing is recovered from the work directory without running
PixInsight again (`publish_intents`).

## Not verified yet (phase 0 spike)

The scripts under `src/altair/pjsr/` follow the PixInsight 1.8.9/1.9 process
parameters. They have **not** been run on a real installation, so the following
need checking on the processing PC:

- The exact CLI flag spelling, and whether `-r` arguments reach `jsArguments` on
  Windows.
- Whether an automation instance can run while an interactive PixInsight is open.
- The `SubframeSelector.measurements` column layout (`lib/measure.js`).
- The `ImageIntegration`, `StarAlignment` and `LocalNormalization` output properties.

Headless WBPP driving (`engine: wbpp`) depends on WBPP internals. Until it is
verified, `wbpp_driver.js` runs the native pipeline, which follows the same contract.
That is also why `pixinsight.night_stack_engine` defaults to `native`.

In tests, `tests/fake_pixinsight.py` stands in for PixInsight and follows this
contract.
