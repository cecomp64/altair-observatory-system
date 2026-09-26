"""altair.yaml (SPEC §5), validated at startup.

Every section has defaults, so a small file (a site, a rig, the Hub block)
is enough for the Hub integration and `altair index`; the storage, collector
and processing sections add what the pipeline needs. Unknown keys are kept
(``extra="allow"``) so a newer file still loads.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_HEADER_MAPPING: dict[str, list[str]] = {
    "image_type": ["IMAGETYP"], "telescope": ["TELESCOP"], "camera": ["INSTRUME"], "focal_length": ["FOCALLEN"],
    "filter": ["FILTER"], "target": ["OBJECT"], "exposure": ["EXPOSURE", "EXPTIME"], "gain": ["GAIN"],
    "offset": ["OFFSET"], "sensor_temp": ["CCD-TEMP"], "xbinning": ["XBINNING"], "ybinning": ["YBINNING"],
    "readout_mode": ["READOUTM"], "bayer_pattern": ["BAYERPAT"], "date_obs": ["DATE-OBS"],
    "ra": ["RA", "OBJCTRA"], "dec": ["DEC", "OBJCTDEC"], "width": ["NAXIS1"], "height": ["NAXIS2"],
    "rotation": ["OBJCTROT", "POSANGLE"],
}

DATA_CLASSES = ("raw_light", "raw_calibration", "calibration_master", "project_reference", "calibrated_frame",
                "night_master", "multi_night_master", "registered_frame", "provisional", "metadata")


class Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class Site(Loose):
    name: str = "Observatory"
    latitude: float
    longitude: float
    timezone: str
    session_rollover_local: str = "12:00"


class Paths(Loose):
    state: str = "C:/ProgramData/Altair/state"
    work: str | None = None        # per-job scratch incl. every WBPP intermediate (local NVMe)
    spool: str | None = None       # collector staging before the NAS write (local NVMe)
    cache: str | None = None       # superseded by storage.cache.path
    published: str | None = None   # user-facing viewing copies of masters (usually on the NAS)

    def _under_state(self, value: str | None, name: str) -> Path:
        return Path(value) if value else Path(self.state).parent / name

    @property
    def work_dir(self) -> Path:
        return self._under_state(self.work, "work")

    @property
    def spool_dir(self) -> Path:
        return self._under_state(self.spool, "spool")

    @property
    def logs_dir(self) -> Path:
        return Path(self.state) / "logs"


# ── storage (§7) ─────────────────────────────────────────────────────────
class CacheConfig(Loose):
    path: str | None = None
    max_size_gb: float = 200
    min_free_gb: float = 50
    pin: list[str] = Field(default_factory=lambda: ["calibration_master", "project_reference", "night_master", "multi_night_master"])


class HealthCheck(Loose):
    identity_file: str = ".altair-location.json"
    max_missing_sample_percent: float = 1
    sample_size: int = 200


class S3Credentials(Loose):
    profile: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


class LifecycleRule(Loose):
    to: str
    after_days: int


class ObjectLock(Loose):
    mode: Literal["GOVERNANCE", "COMPLIANCE"] = "GOVERNANCE"
    retain_years: int = 10
    classes: list[str] = Field(default_factory=lambda: ["raw_light", "calibrated_frame", "calibration_master",
                                                        "project_reference", "night_master"])


class RestoreConfig(Loose):
    tier: Literal["Bulk", "Standard", "Expedited"] = "Bulk"
    days: int = 7
    max_auto_restore_gb: float = 50


class Location(Loose):
    name: str
    kind: Literal["fs", "s3"] = "fs"
    durable: bool = True
    read_priority: int = 10
    # fs
    root: str | None = None
    credential_target: str | None = None
    stores: list[str] = Field(default_factory=lambda: ["raw_light", "raw_calibration", "calibration_master", "project_reference",
                                                       "calibrated_frame", "night_master", "multi_night_master", "metadata"])
    write_verify: Literal["readback", "none"] = "readback"
    read_in_place: bool = True
    stage_raw_locally: bool = False
    nas_read_verify: Literal["none", "sample", "all"] = "none"
    min_free_percent: float = 10
    health_check: HealthCheck = Field(default_factory=HealthCheck)
    # s3
    bucket: str | None = None
    prefix: str = "altair/"
    region: str | None = None
    endpoint_url: str | None = None   # S3-compatible stores (MinIO, B2, Wasabi)
    credentials: S3Credentials = Field(default_factory=S3Credentials)
    backup_classes: list[str] = Field(default_factory=lambda: ["raw_light", "calibration_master", "project_reference",
                                                               "calibrated_frame", "night_master", "multi_night_master", "metadata"])
    storage_class: dict[str, str] = Field(default_factory=lambda: {"raw_light": "STANDARD_IA", "calibrated_frame": "STANDARD_IA",
                                                                   "default": "STANDARD"})
    lifecycle: dict[str, LifecycleRule | int] = Field(default_factory=lambda: {
        "raw_light": LifecycleRule(to="DEEP_ARCHIVE", after_days=120),
        "calibrated_frame": LifecycleRule(to="GLACIER_IR", after_days=60),
        "abort_incomplete_multipart_after_days": 7})
    object_lock: ObjectLock | None = None
    restore: RestoreConfig = Field(default_factory=RestoreConfig)
    max_auto_download_gb: float = 200
    upload_bandwidth_limit_mbps: float | None = None
    upload_window: str | None = None
    multipart_chunk_mb: int = 64

    def class_storage(self, data_class: str) -> str:
        return self.storage_class.get(data_class, self.storage_class.get("default", "STANDARD"))


class Backup(Loose):
    raw_first: bool = True
    start: Literal["on_collect", "after_session"] = "on_collect"
    processing_waits_for_raw_backup: bool = False
    raw_backup_sla_hours: float = 6
    data_at_risk_hours: float = 48
    calibrated_frame_stage: Literal["calibrated", "cosmetized", "debayered"] = "calibrated"
    calibrated_frame_compression: str = "zstd+shuffle"


class CleanupRule(Loose):
    min_age_days: float | None = None
    require: list[str] = Field(default_factory=list)


class RigCleanup(Loose):
    enabled: bool = True
    raw_light: CleanupRule = Field(default_factory=lambda: CleanupRule(min_age_days=3, require=["nas_verified", "s3_verified"]))
    raw_calibration: CleanupRule = Field(default_factory=lambda: CleanupRule(min_age_days=3, require=["nas_verified"]))
    target_free_percent: float = 20
    keep: list[str] = Field(default_factory=lambda: ["_altair/**"])
    markers_keep_days: float = 30


class NasCleanup(Loose):
    raw_light: CleanupRule = Field(default_factory=lambda: CleanupRule(
        min_age_days=180, require=["s3_verified_fresh", "night_processed", "no_open_issue", "project_idle_days_60",
                                   "not_frame_reintegration_project"]))
    calibrated_frame: CleanupRule = Field(default_factory=lambda: CleanupRule(
        min_age_days=180, require=["s3_verified_fresh", "no_open_issue", "project_idle_days_60", "not_frame_reintegration_project"]))
    raw_calibration: CleanupRule = Field(default_factory=lambda: CleanupRule(
        min_age_days=365, require=["master_nas_verified", "master_s3_verified", "no_open_issue"]))
    target_free_percent: float = 15
    pin_projects: list[str] = Field(default_factory=list)


class CatalogBackupsKeep(Loose):
    daily: int = 30
    monthly: int = 12


class S3Cleanup(Loose):
    multi_night_master_versions_keep: int = 20
    catalog_backups_keep: CatalogBackupsKeep = Field(default_factory=CatalogBackupsKeep)


class WorkCleanup(Loose):
    succeeded_jobs: Literal["delete_immediately", "keep"] = "delete_immediately"
    failed_jobs_keep_days: float = 7


class PublishedCleanup(Loose):
    night_masters_keep_days: float = 365
    multi_night_versions_keep: int = 3


class Cleanup(Loose):
    schedule_local: str = "10:30"
    dry_run: bool = False
    rig_defaults: RigCleanup = Field(default_factory=RigCleanup)
    work: WorkCleanup = Field(default_factory=WorkCleanup)
    published: PublishedCleanup = Field(default_factory=PublishedCleanup)
    logs_keep_days: float = 90
    nas: NasCleanup = Field(default_factory=NasCleanup)
    s3: S3Cleanup = Field(default_factory=S3Cleanup)


class Verify(Loose):
    s3_after_upload: Literal["checksum", "head_only"] = "checksum"
    scrub_interval_days: float = 90
    scrub_sample_percent: float = 2


class Storage(Loose):
    cache: CacheConfig = Field(default_factory=CacheConfig)
    locations: list[Location] = Field(default_factory=list)
    backup: Backup = Field(default_factory=Backup)
    cleanup: Cleanup = Field(default_factory=Cleanup)
    verify: Verify = Field(default_factory=Verify)

    def location(self, name: str) -> Location | None:
        return next((loc for loc in self.locations if loc.name == name), None)

    @property
    def nas(self) -> Location | None:
        return self.location("nas")

    @property
    def s3(self) -> Location | None:
        return next((loc for loc in self.locations if loc.kind == "s3"), None)


# ── rigs (§5) ────────────────────────────────────────────────────────────
class Rotator(Loose):
    present: bool = False
    source: Literal["header", "filename"] = "header"
    keywords: list[str] = Field(default_factory=lambda: ["ROTATOR"])
    filename_regex: str | None = None
    units: Literal["degrees", "steps"] = "degrees"
    steps_per_revolution: float | None = None
    tolerance: float = 1.0
    require_on_lights_and_flats: bool = True


class RigHub(Loose):
    telescope: str
    optical_train: str


class Collect(Loose):
    poll_interval_s: float = 60
    stable_seconds: float = 60
    during_capture: bool = True
    verify: Literal["double_read", "single_read"] = "double_read"
    bandwidth_limit_mbps: float | None = None
    parallel_files: int = 2
    stuck_minutes: float = 30


class Rig(Loose):
    hub: RigHub | None = None
    host: str | None = None
    raw_root: str | None = None
    credential_target: str | None = None
    include: list[str] = Field(default_factory=lambda: ["**/*.fits", "**/*.fit", "**/*.xisf"])
    exclude: list[str] = Field(default_factory=lambda: ["_altair/**", "**/*.tmp", "**/Snapshots/**"])
    collect: Collect = Field(default_factory=Collect)
    session_end_marker: str = "_altair/session-end-*.json"
    quiescence_minutes: float = 45
    unreachable_alert_minutes: float = 30
    cleanup: dict = Field(default_factory=dict)   # overrides of storage.cleanup.rig_defaults
    telescope: str
    camera: str
    focal_length_mm: float
    focal_length_tolerance_mm: float = 10
    rotator: Rotator = Field(default_factory=Rotator)
    default_filter: str | None = None


class Camera(Loose):
    type: Literal["mono", "osc"] = "mono"
    cooled: bool = True
    bayer_pattern: str | None = None


# ── processing (§5, §8, §9) ──────────────────────────────────────────────
class Triggers(Loose):
    require_after_dawn: bool = True
    scheduled_fallback_local: str = "09:00"
    min_lights_per_stack: int = 5


class PixInsight(Loose):
    executable: str = "C:/Program Files/PixInsight/bin/PixInsight.exe"
    wbpp_dir: str = "C:/Program Files/PixInsight/src/scripts/BatchProcessing/WeightedBatchPreprocessing"
    runner: str | None = None          # altair_runner.js; default: the pjsr/ folder shipped with Altair
    instance_slot: int = 5
    extra_args: list[str] = Field(default_factory=lambda: ["--automation-mode", "--no-startup-scripts", "--force-exit"])
    priority: Literal["below_normal", "normal", "idle"] = "below_normal"
    timeout_minutes: float = 360
    max_concurrent_jobs: int = 1
    max_attempts: int = 3
    tested_versions: list[str] = Field(default_factory=lambda: ["1.9.3"])
    night_stack_engine: Literal["wbpp", "native"] = "native"   # "wbpp" once WBPP driving is verified (phase 0)


class DarkMatch(Loose):
    exposure_tolerance_s: float = 0.0
    temp_tolerance_c: float = 2.0
    max_age_days: float = 180


class BiasMatch(Loose):
    max_age_days: float = 365


class DarkflatMatch(Loose):
    exposure_tolerance_s: float = 0.1
    max_age_days: float = 180


class FlatMatch(Loose):
    max_age_days: float = 60
    prefer: list[Literal["same_night", "nearest_after", "nearest_before"]] = Field(
        default_factory=lambda: ["same_night", "nearest_after", "nearest_before"])


class CalibrationMatching(Loose):
    dark: DarkMatch = Field(default_factory=DarkMatch)
    bias: BiasMatch = Field(default_factory=BiasMatch)
    darkflat: DarkflatMatch = Field(default_factory=DarkflatMatch)
    flat: FlatMatch = Field(default_factory=FlatMatch)
    flat_dark_strategy: Literal["darkflat_preferred", "bias_only", "darkflat_only"] = "darkflat_preferred"


class NightProcessing(Loose):
    produce_provisional_without_flat: bool = True
    wbpp_profile: str = "default"
    max_rejected_fraction: float = 0.5


class MultiNight(Loose):
    enabled: bool = True
    mode: Literal["master_merge", "frame_reintegration"] = "master_merge"
    night_weighting: Literal["measured_psf_signal", "inverse_noise_variance", "frame_weight_sum"] = "measured_psf_signal"
    normalization: Literal["local", "global"] = "local"
    rejection: Literal["none", "winsorized_sigma"] = "none"
    min_nights: int = 1
    max_fwhm_ratio_to_project_median: float | None = 1.6
    min_overlap_fraction: float = 0.8
    rebuild: Literal["from_all_nights"] = "from_all_nights"
    keep_versions: int = 10
    autocrop_output: bool = True
    min_coverage_nights: int | Literal["all"] = "all"
    reference_filter: list[str] = Field(default_factory=lambda: ["L", "Ha"])


class Issues(Loose):
    page: str | None = None
    remind_every_days: float = 3
    auto_rerun_on_resolution: bool = True


class Notifications(Loose):
    on: list[str] = Field(default_factory=lambda: ["success", "partial", "failure", "issue_opened", "issue_resolved"])
    channels: list[dict] = Field(default_factory=lambda: [{"type": "windows_toast"}])


# ── Hub (§5.1, §17) ──────────────────────────────────────────────────────
class HubResolve(Loose):
    by_header_token: bool = True
    by_name: bool = True
    by_coordinates: bool = True
    max_offset_fov_fraction: float = 0.5


class HubOutbox(Loose):
    batch_size: int = Field(500, ge=1, le=500)
    max_backoff_s: float = 900


class HubPreviews(Loose):
    enabled: bool = True
    long_edge_px: int = 2048
    thumb_px: int = 512
    jpeg_quality: int = 85


class Hub(Loose):
    enabled: bool = False
    base_url: str = ""
    node: str = ""
    credential_target: str = "altair-hub"
    config_poll_s: float = 300
    command_poll_s: float = 60
    unreachable_alert_minutes: float = 60
    require_target_link: bool = True
    resolve: HubResolve = Field(default_factory=HubResolve)
    outbox: HubOutbox = Field(default_factory=HubOutbox)
    previews: HubPreviews = Field(default_factory=HubPreviews)
    frame_headers: Literal["full", "summary"] = "full"

    def api_key(self) -> str | None:
        """The node key: ALTAIR_HUB_API_KEY, else Windows Credential Manager."""
        if key := os.environ.get("ALTAIR_HUB_API_KEY"):
            return key
        try:
            import keyring

            return keyring.get_password(self.credential_target, self.node) or keyring.get_password(self.credential_target, "api_key")
        except Exception:  # noqa: BLE001 - no keyring backend on this machine
            return None


class AltairConfig(Loose):
    site: Site
    paths: Paths = Field(default_factory=Paths)
    storage: Storage = Field(default_factory=Storage)
    pixinsight: PixInsight = Field(default_factory=PixInsight)
    triggers: Triggers = Field(default_factory=Triggers)
    rigs: dict[str, Rig] = Field(default_factory=dict)
    aliases: dict[str, dict[str, str]] = Field(default_factory=dict)
    cameras: dict[str, Camera] = Field(default_factory=dict)
    header_mapping: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_HEADER_MAPPING))
    calibration_matching: CalibrationMatching = Field(default_factory=CalibrationMatching)
    night_processing: NightProcessing = Field(default_factory=NightProcessing)
    wbpp: dict = Field(default_factory=lambda: {"profiles": {"default": {}}})
    multi_night: MultiNight = Field(default_factory=MultiNight)
    issues: Issues = Field(default_factory=Issues)
    notifications: Notifications = Field(default_factory=Notifications)
    hub: Hub = Field(default_factory=Hub)

    @field_validator("header_mapping")
    @classmethod
    def _merge_defaults(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        return {**DEFAULT_HEADER_MAPPING, **value}

    @property
    def catalog_path(self) -> Path:
        return Path(self.paths.state) / "catalog.sqlite"

    @property
    def cache_dir(self) -> Path:
        path = self.storage.cache.path or self.paths.cache
        return Path(path) if path else Path(self.paths.state).parent / "cache"

    def camera(self, name: str | None) -> Camera | None:
        return self.cameras.get(name or "")

    def rig_cleanup(self, rig: str) -> RigCleanup:
        overrides = self.rigs[rig].cleanup
        base = self.storage.cleanup.rig_defaults.model_dump()
        for key, value in overrides.items():
            base[key] = {**base[key], **value} if isinstance(value, dict) and isinstance(base.get(key), dict) else value
        return RigCleanup.model_validate(base)

    def rig_for_train(self, telescope: str, optical_train: str) -> str | None:
        for name, rig in self.rigs.items():
            if rig.hub and rig.hub.telescope == telescope and rig.hub.optical_train == optical_train:
                return name
        return None

    def rig_for_equipment(self, telescope: str | None, camera: str | None) -> str | None:
        for name, rig in self.rigs.items():
            if rig.telescope == telescope and rig.camera == camera:
                return name
        return None


def load(path: str | os.PathLike[str]) -> AltairConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AltairConfig.model_validate(data)
