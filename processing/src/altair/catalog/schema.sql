-- Altair catalog (SPEC §6.3), with the Hub columns and tables of SPEC §17.5.
-- Components are built in phases; tables for later phases are created here so
-- every phase shares one schema.

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- ── Storage (§7) ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS blobs (
  sha256 TEXT PRIMARY KEY,
  size_bytes INTEGER NOT NULL,
  data_class TEXT NOT NULL,
  logical_path TEXT NOT NULL,
  origin_rig TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(logical_path, sha256)
);

CREATE TABLE IF NOT EXISTS locations (
  name TEXT PRIMARY KEY,           -- rig:<name> / nas / cache / s3 / external:<name>
  kind TEXT NOT NULL,              -- fs / s3
  durable INTEGER NOT NULL,
  reachable INTEGER NOT NULL DEFAULT 1,
  read_only INTEGER NOT NULL DEFAULT 0,
  last_probe_at TEXT
);

CREATE TABLE IF NOT EXISTS replicas (
  sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  location TEXT NOT NULL REFERENCES locations(name),
  uri TEXT NOT NULL,
  state TEXT NOT NULL,
  storage_class TEXT,
  restore_expires_at TEXT,
  verified_at TEXT,
  verify_method TEXT,              -- sha256_full / s3_checksum_sha256 / size_only
  last_seen_at TEXT,
  version_id TEXT,                 -- S3 object version the replica refers to
  missing_reason TEXT,             -- cleanup / external_delete / never_arrived
  PRIMARY KEY (sha256, location)
);

-- Staging and restore bookkeeping (§7.7).
CREATE TABLE IF NOT EXISTS fetch_requests (
  id INTEGER PRIMARY KEY,
  job_id INTEGER,
  sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  source_location TEXT,
  state TEXT NOT NULL,             -- pending / restoring / downloading / done / failed / awaiting_approval
  bytes_done INTEGER DEFAULT 0,
  error TEXT, updated_at TEXT
);

-- Every deletion Altair ever performs (§7.6).
CREATE TABLE IF NOT EXISTS cleanup_ledger (
  id INTEGER PRIMARY KEY,
  sha256 TEXT, location TEXT NOT NULL, uri TEXT NOT NULL,
  rule TEXT NOT NULL,              -- e.g. rig:esprit100_2600mm.raw_light
  relied_on_json TEXT NOT NULL,    -- the verified backup replica(s) checked right before deleting
  bytes INTEGER,
  deleted_at TEXT NOT NULL, dry_run INTEGER NOT NULL
);

-- What the collector has seen in each rig's raw root (§7.3).
CREATE TABLE IF NOT EXISTS rig_files (
  rig TEXT NOT NULL, rel_path TEXT NOT NULL,
  size INTEGER, mtime REAL, first_seen_at TEXT, stable_since TEXT,
  state TEXT NOT NULL,             -- seen / collecting / collected / stuck / excluded / gone / cleaned
  sha256 TEXT,
  night TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  PRIMARY KEY (rig, rel_path)
);
CREATE INDEX IF NOT EXISTS rig_files_state ON rig_files(rig, state);

-- Verified frames waiting in the collector spool until S3 no longer needs them (§7.3).
CREATE TABLE IF NOT EXISTS spool_files (
  sha256 TEXT PRIMARY KEY, path TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collections (
  rig TEXT NOT NULL, night TEXT NOT NULL,
  state TEXT NOT NULL,             -- open / closing / closed
  closed_by TEXT,                  -- session_end_marker / session_end / quiescence / scheduled / manual
  n_files INTEGER, total_bytes INTEGER,
  manifest_sha256 TEXT,
  opened_at TEXT, closed_at TEXT, session_end_at TEXT,
  last_frame_at TEXT,              -- newest collected frame (quiescence, §6.1)
  PRIMARY KEY (rig, night)
);

-- ── Science catalog ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS frames (
  id INTEGER PRIMARY KEY,
  sha256 TEXT UNIQUE NOT NULL REFERENCES blobs(sha256),
  image_type TEXT NOT NULL, night TEXT NOT NULL, date_obs TEXT NOT NULL,
  rig TEXT, telescope TEXT, camera TEXT, filter TEXT, target TEXT,
  focal_length REAL, exposure REAL, gain INTEGER, offset INTEGER, sensor_temp REAL,
  binning TEXT, readout_mode TEXT, width INTEGER, height INTEGER, bayer_pattern TEXT,
  rotator_pos REAL, rotator_units TEXT,
  ra_deg REAL, dec_deg REAL, rotation_deg REAL,
  raw_headers_json TEXT,
  status TEXT NOT NULL,            -- collected/valid/invalid/held/processed/rejected
  status_reason TEXT,
  quality_json TEXT,
  origin TEXT NOT NULL DEFAULT 'collect',   -- collect / import / legacy_index
  file_name TEXT,
  hub_target_id INTEGER,           -- NULL = unlinked (§17.5)
  assignment_source TEXT,          -- header_token / name / coords / manual / unlinked
  hub_synced_at TEXT
);
CREATE INDEX IF NOT EXISTS frames_rig_night ON frames(rig, night);
CREATE INDEX IF NOT EXISTS frames_hub_target ON frames(hub_target_id);

CREATE TABLE IF NOT EXISTS calibration_masters (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  sha256 TEXT UNIQUE NOT NULL,
  source_frames_json TEXT,
  camera TEXT NOT NULL, telescope TEXT, filter TEXT, focal_length REAL,
  exposure REAL, gain INTEGER, offset INTEGER, sensor_temp REAL,
  binning TEXT, readout_mode TEXT, width INTEGER, height INTEGER,
  rotator_pos REAL, rotator_units TEXT, rig TEXT,
  night TEXT, n_frames INTEGER,
  superseded_by INTEGER REFERENCES calibration_masters(id),
  quality_json TEXT,
  taken_at TEXT,                   -- median DATE-OBS of its subs: equipment events split validity here (§8.3)
  job_id INTEGER, imported INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS equipment_events (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL, rig TEXT NOT NULL, kind TEXT NOT NULL, filter TEXT, note TEXT,
  hub_event_id INTEGER UNIQUE
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  target TEXT NOT NULL, telescope TEXT NOT NULL, camera TEXT NOT NULL,
  reference_sha256 TEXT, reference_night TEXT, reference_version INTEGER NOT NULL DEFAULT 1,
  pixel_scale_arcsec REAL, drizzle_scale INTEGER NOT NULL DEFAULT 1,
  rig TEXT, hub_target_id INTEGER, hub_project_id INTEGER, settings_json TEXT,
  multi_night_mode TEXT NOT NULL DEFAULT 'master_merge',
  path TEXT,                       -- logical path root, fixed at creation (§6.6: renames never move archives)
  created_at TEXT,
  UNIQUE(target, telescope, camera)
);

-- Manual merge decisions (§10.4 `altair night include|exclude`).
CREATE TABLE IF NOT EXISTS night_decisions (
  project_id INTEGER NOT NULL, night TEXT NOT NULL, filter TEXT NOT NULL,
  decision TEXT NOT NULL,          -- include / exclude
  source TEXT, decided_at TEXT,
  PRIMARY KEY (project_id, night, filter)
);
CREATE UNIQUE INDEX IF NOT EXISTS projects_hub ON projects(hub_target_id, rig) WHERE hub_target_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS night_masters (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  night TEXT NOT NULL, filter TEXT NOT NULL, sha256 TEXT NOT NULL,
  input_frames_json TEXT NOT NULL, kind TEXT NOT NULL, reference_version INTEGER NOT NULL,
  n_frames INTEGER, n_rejected INTEGER, total_exposure_s REAL,
  calib_json TEXT NOT NULL DEFAULT '{}', flat_verified INTEGER NOT NULL DEFAULT 0,
  metrics_json TEXT, night_weight REAL,
  merge_status TEXT NOT NULL DEFAULT 'eligible', merge_block_reason TEXT,
  job_id INTEGER, superseded_by INTEGER REFERENCES night_masters(id),
  archive_uri TEXT, nas_path TEXT, size_bytes INTEGER,
  UNIQUE(project_id, night, filter, kind, reference_version, sha256)
);

CREATE TABLE IF NOT EXISTS multi_night_masters (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  filter TEXT NOT NULL, version INTEGER NOT NULL, sha256 TEXT NOT NULL,
  inputs_json TEXT NOT NULL, excluded_json TEXT NOT NULL DEFAULT '[]',
  total_exposure_s REAL, n_nights INTEGER, plan_hash TEXT UNIQUE NOT NULL, created_at TEXT,
  archive_uri TEXT, nas_path TEXT, size_bytes INTEGER, input_frames_json TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,              -- CALIB_MASTER / PROJECT_REFERENCE / NIGHT_STACK / MERGE
  scope_json TEXT NOT NULL, plan_json TEXT NOT NULL, plan_hash TEXT UNIQUE NOT NULL,
  depends_on_json TEXT,            -- job ids that must succeed first
  status TEXT NOT NULL,            -- queued / staging / waiting_data / running / succeeded / failed / skipped / blocked / superseded
  attempts INTEGER DEFAULT 0,
  started_at TEXT, finished_at TEXT, log_path TEXT, error TEXT,
  project_id INTEGER, night TEXT, filter TEXT, rig TEXT,
  not_before TEXT,                 -- retry back-off
  waiting_reason TEXT,             -- why a job waits for data (§7.7)
  result_json TEXT,                -- the executor's result.json, outputs registered
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL,
  fingerprint TEXT UNIQUE NOT NULL,
  scope_json TEXT NOT NULL,        -- rig, night, target, filter, frame ids, hub_target_id
  requirement_json TEXT, message TEXT NOT NULL,
  created_at TEXT, last_notified_at TEXT, resolved_at TEXT, resolution TEXT, rerun_job_ids_json TEXT
);

-- Requests the planner consumes: re-plans after assignments, reruns,
-- include/exclude, re-reference, mode changes (from the CLI or Hub commands).
CREATE TABLE IF NOT EXISTS plan_requests (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL, payload_json TEXT NOT NULL, source TEXT NOT NULL,
  created_at TEXT NOT NULL, done_at TEXT
);

-- ── Hub sync (§17.5) ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hub_outbox (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL, natural_key TEXT NOT NULL, payload_json TEXT NOT NULL,
  attachment_paths_json TEXT,
  created_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT, sent_at TEXT, parked INTEGER NOT NULL DEFAULT 0, last_error TEXT
);
CREATE INDEX IF NOT EXISTS hub_outbox_pending ON hub_outbox(sent_at, parked, next_attempt_at);

CREATE TABLE IF NOT EXISTS hub_commands (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL, payload_json TEXT NOT NULL,
  received_at TEXT NOT NULL, executed_at TEXT, state TEXT NOT NULL, result_json TEXT, acked_at TEXT
);

CREATE TABLE IF NOT EXISTS hub_cache (
  key TEXT PRIMARY KEY, etag TEXT, payload_json TEXT NOT NULL, fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hub_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
