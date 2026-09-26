"""Planning a rig-night (SPEC §6.4).

1. Calibration subs become CALIB_MASTER jobs (bias and dark-flats, then
   darks, then flats; a flat master is calibrated with its own dark-flat or
   bias). Masters these jobs will build count as candidates for this night.
2. Lights are grouped by stack key (linked projects: rig, Hub target,
   filter) and split by exposure, gain, offset, binning, readout, size and
   rotator position. Each group is matched against the calibration library
   (§8). What is missing becomes an issue whose ``requirement_json`` lets a
   future master resolve it automatically (§10.4).
3. A stack key whose every group is fully calibrated gets a final
   NIGHT_STACK; one missing only flats gets a provisional no-flat preview
   (never merged) and its lights are held. A project without a reference
   gets a PROJECT_REFERENCE job first.
4. Every job carries a ``plan_hash`` (SHA-256 of its canonical plan); a
   plan that already succeeded is not run again. A newer plan for the same
   stack supersedes a queued older one.

Planning never reads image data: headers are cached in the catalog.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from altair import __version__
from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.hub.config_sync import HubConfig
from altair.hub.reporters import enqueue_frame_patch, enqueue_job
from altair.issues import raise_issue, resolve_issue
from altair.planner import matching
from altair.planner.matching import Match, Miss
from altair.planner.projects import project_for, project_label, settings

log = logging.getLogger("altair.planner")
PLANNER_VERSION = 1
CALIB_KINDS = {"bias": "BIAS", "darkflat": "DARKFLAT", "dark": "DARK", "flat": "FLAT"}
CALIB_ORDER = ["BIAS", "DARKFLAT", "DARK", "FLAT"]


def plan_hash(plan: dict[str, Any]) -> str:
    canonical = json.dumps({**plan, "software": {"altair": __version__, "planner": PLANNER_VERSION}}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class LightGroup:
    project_id: int
    filter: str
    frames: list[sqlite3.Row]
    need: dict[str, Any]
    dark: Match | Miss | None = None
    flat: Match | Miss | None = None

    @property
    def calibrated(self) -> bool:
        return isinstance(self.dark, Match) and isinstance(self.flat, Match)

    @property
    def misses(self) -> list[Miss]:
        return [m for m in (self.dark, self.flat) if isinstance(m, Miss)]


@dataclass
class NightPlan:
    rig: str
    night: str
    calib_jobs: list[dict] = field(default_factory=list)
    groups: list[LightGroup] = field(default_factory=list)
    stacks: list[dict] = field(default_factory=list)
    reference_jobs: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def jobs(self) -> list[dict]:
        return self.calib_jobs + self.reference_jobs + self.stacks


def _median(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return float(statistics.median(clean)) if clean else None


def _first(rows, key):
    return next((r[key] for r in rows if r[key] is not None), None)


def _ref(master: dict) -> dict:
    """How a job refers to a master: its SHA-256, or the job building it."""
    return {"sha256": master["sha256"]} if master.get("sha256") else {"job": master["pending_job"]}


def resolve_plan(plan: dict[str, Any], resolve: Callable[[dict, str], str | None]) -> dict[str, Any]:
    """The plan with every master/reference ref given by SHA-256 (as a plan
    made after those jobs succeeded would have it)."""
    out = json.loads(json.dumps(plan))

    def fix(ref: dict | None, role: str) -> dict | None:
        if not ref or "job" not in ref:
            return ref
        sha = resolve(ref, role)
        if role == "reference":
            return {"sha256": sha, "version": out.get("reference_version")}
        return {"sha256": sha}

    for kind, ref in (out.get("calibrate_with") or {}).items():
        out["calibrate_with"][kind] = fix(ref, "master")
    for group in out.get("groups") or []:
        for key in ("dark", "flat"):
            if key in group:
                group[key] = fix(group[key], "master")
    if "reference" in out:
        out["reference"] = fix(out["reference"], "reference")
    return out


def insert_job(tx: sqlite3.Connection, config: AltairConfig, job: dict, ids: dict[str, int], rig: str | None) -> int:
    """Insert a planned job once (by ``plan_hash``); a failed or superseded
    job with the same plan is queued again. A newer NIGHT_STACK or MERGE plan
    for the same stack supersedes a queued older one."""
    # A plan that refers to masters by SHA-256 is the same work as an earlier
    # plan that referred to the jobs building them (resolved_hash).
    existing = tx.execute("SELECT id, status FROM jobs WHERE plan_hash = ? OR resolved_hash = ? ORDER BY plan_hash = ? DESC, id DESC",
                          (job["plan_hash"], job["plan_hash"], job["plan_hash"])).fetchone()
    if existing:
        if existing["status"] in ("failed", "superseded", "skipped", "blocked"):
            tx.execute("UPDATE jobs SET status = 'queued', attempts = 0, error = NULL, not_before = NULL, waiting_reason = NULL WHERE id = ?",
                       (existing["id"],))
            if config.hub.enabled:
                enqueue_job(tx, existing["id"])
        return existing["id"]
    depends = []
    for dep in job.get("depends", []):
        dep_id = ids.get(dep) or (tx.execute("SELECT id FROM jobs WHERE plan_hash = ?", (dep,)).fetchone() or {"id": None})["id"]
        if dep_id:
            depends.append(dep_id)
    job_id = tx.execute(
        "INSERT INTO jobs(kind, scope_json, plan_json, plan_hash, depends_on_json, status, project_id, night, filter, rig, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
        (job["kind"], json.dumps(job["scope"]), json.dumps(job["plan"], sort_keys=True), job["plan_hash"], json.dumps(depends),
         job.get("project_id"), job.get("night"), job.get("filter"), rig, now_iso())).lastrowid
    superseded = []
    if job["kind"] == "NIGHT_STACK":
        superseded = tx.execute(
            "SELECT id FROM jobs WHERE kind = 'NIGHT_STACK' AND project_id = ? AND night = ? AND filter = ? AND id != ? "
            "AND status IN ('queued', 'waiting_data', 'blocked') AND json_extract(scope_json, '$.stack_kind') = ?",
            (job["project_id"], job["night"], job["filter"], job_id, job["scope"]["stack_kind"])).fetchall()
    elif job["kind"] == "MERGE":
        superseded = tx.execute("SELECT id FROM jobs WHERE kind = 'MERGE' AND project_id = ? AND filter = ? AND id != ? "
                                "AND status IN ('queued', 'waiting_data', 'blocked')", (job["project_id"], job["filter"], job_id)).fetchall()
    for row in superseded:
        tx.execute("UPDATE jobs SET status = 'superseded' WHERE id = ?", (row["id"],))
        if config.hub.enabled:
            enqueue_job(tx, row["id"])
    if config.hub.enabled:
        enqueue_job(tx, job_id)
    return job_id


class Planner:
    def __init__(self, catalog: Catalog, config: AltairConfig, hub_config: Callable[[], HubConfig | None] = lambda: None,
                 *, clock: Callable[[], datetime] | None = None):
        self.catalog = catalog
        self.config = config
        self.hub_config = hub_config
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ── inputs ───────────────────────────────────────────────────────────
    def _library(self) -> list[dict]:
        return [dict(r) for r in self.catalog.query("SELECT * FROM calibration_masters WHERE superseded_by IS NULL")]

    def _events(self) -> list[dict]:
        return [dict(r) for r in self.catalog.query("SELECT * FROM equipment_events")]

    def _eligible_light(self, frame: sqlite3.Row) -> bool:
        if frame["status"] not in ("valid", "held", "processed"):
            return False
        if frame["hub_target_id"] is not None:
            return True
        return not (self.config.hub.enabled and self.config.hub.require_target_link)

    # ── planning a night ─────────────────────────────────────────────────
    def plan_night(self, rig: str, night: str, *, dry_run: bool = False) -> NightPlan:
        plan = NightPlan(rig, night)
        rig_cfg = self.config.rigs[rig]
        if self.catalog.one("SELECT 1 FROM issues WHERE fingerprint = ? AND status = 'open'", (f"HUB_CONFIG_MISMATCH:{rig}",)):
            plan.skipped.append(f"HUB_CONFIG_MISMATCH is open for {rig}")
            return plan
        frames = self.catalog.query("SELECT f.*, b.size_bytes, b.logical_path FROM frames f JOIN blobs b USING (sha256) "
                                    "WHERE f.rig = ? AND f.night = ? ORDER BY f.date_obs", (rig, night))
        library = self._library()
        events = self._events()
        pending = self._plan_calibration(plan, [f for f in frames if f["image_type"] != "light" and f["status"] == "valid"], library)
        candidates = library + pending
        lights = [f for f in frames if f["image_type"] == "light" and self._eligible_light(f)]
        self._check_focal_length(plan, rig, night, lights)
        with self.catalog.transaction() as tx:
            projects = {}
            for f in lights:
                key = ("hub", f["hub_target_id"]) if f["hub_target_id"] is not None else ("text", f["target"])
                if key not in projects:
                    projects[key] = project_for(tx, self.config, self.hub_config(), rig=rig,
                                                hub_target_id=f["hub_target_id"], target_text=f["target"])
        for group in self._light_groups(rig, lights, projects):
            group.dark = matching.match_dark(group.need, candidates, self.config.calibration_matching,
                                             cooled=self._cooled(rig_cfg.camera))
            group.flat = matching.match_flat(group.need, candidates, self.config.calibration_matching, rig_cfg.rotator,
                                             focal_tolerance=rig_cfg.focal_length_tolerance_mm, events=events)
            if isinstance(group.flat, Miss):
                group.flat = self._flat_override(rig, night, group, candidates) or group.flat
            plan.groups.append(group)
        self._plan_stacks(plan, {p["id"]: p for p in projects.values()})
        if not dry_run:
            self._commit(plan)
        return plan

    def _flat_override(self, rig: str, night: str, group: LightGroup, candidates: list[dict]) -> Match | None:
        """`altair issue resolve --flat … --force-match` (§10.4): the user's flat, recorded as an override."""
        from altair.issue_actions import override_key

        override = self.catalog.get_state(override_key(rig, night, group.filter))
        master = next((m for m in candidates if override and m.get("sha256") == override["sha256"]), None)
        if master is None:
            return None
        return Match(master, {"night": master["night"], "flat_override": True, "override_reason": override["reason"],
                              "issue_id": override["issue_id"]})

    def _cooled(self, camera: str) -> bool:
        cam = self.config.camera(camera)
        return cam.cooled if cam else True

    # ── calibration masters to build ─────────────────────────────────────
    def _plan_calibration(self, plan: NightPlan, subs: list[sqlite3.Row], library: list[dict]) -> list[dict]:
        rig_cfg = self.config.rigs[plan.rig]
        groups: dict[tuple, list[sqlite3.Row]] = {}
        flats = [f for f in subs if f["image_type"] == "flat"]
        buckets = dict(zip([f["id"] for f in flats], matching.rotator_buckets([f["rotator_pos"] for f in flats], rig_cfg.rotator)))
        for f in subs:
            kind = CALIB_KINDS[f["image_type"]]
            sensor = (f["camera"], f["gain"], f["offset"], f["binning"], f["readout_mode"], f["width"], f["height"])
            if kind == "BIAS":
                key = (kind, *sensor)
            elif kind == "DARKFLAT":
                key = (kind, *sensor, round(f["exposure"] or 0, 2))
            elif kind == "DARK":
                key = (kind, *sensor, round(f["exposure"] or 0, 2), round(f["sensor_temp"]) if f["sensor_temp"] is not None else None)
            else:
                key = (kind, plan.rig, f["filter"], f["binning"], f["width"], f["height"], round(f["focal_length"] or 0), buckets.get(f["id"]))
            groups.setdefault(key, []).append(f)
        existing = {frozenset(json.loads(m["source_frames_json"] or "[]")) for m in library}
        pending: list[dict] = []
        for kind in CALIB_ORDER:
            for key, rows in sorted(((k, v) for k, v in groups.items() if k[0] == kind), key=lambda kv: str(kv[0])):
                shas = sorted(r["sha256"] for r in rows)
                if frozenset(shas) in existing:
                    continue   # this exact master exists already
                master = {"kind": kind, "camera": rows[0]["camera"], "telescope": rows[0]["telescope"], "rig": plan.rig,
                          "filter": rows[0]["filter"] if kind == "FLAT" else None, "binning": rows[0]["binning"],
                          "gain": rows[0]["gain"], "offset": rows[0]["offset"], "readout_mode": rows[0]["readout_mode"],
                          "width": rows[0]["width"], "height": rows[0]["height"], "exposure": _median([r["exposure"] for r in rows]),
                          "sensor_temp": _median([r["sensor_temp"] for r in rows]), "focal_length": _median([r["focal_length"] for r in rows]),
                          "rotator_pos": key[-1] if kind == "FLAT" else None, "rotator_units": rig_cfg.rotator.units if kind == "FLAT" else None,
                          "night": plan.night, "n_frames": len(rows), "taken_at": sorted(r["date_obs"] for r in rows)[len(rows) // 2]}
                job_plan = {"kind": "CALIB_MASTER", "master": {k: v for k, v in master.items()}, "inputs": shas,
                            "input_paths": {r["sha256"]: r["logical_path"] for r in rows}}
                if kind == "FLAT":
                    dark = matching.match_flat_dark({**master, "night": plan.night}, library + pending, self.config.calibration_matching)
                    if isinstance(dark, Miss):
                        plan.issues.append(self._calib_issue(plan, dark, master, rows))
                        continue   # a flat master can't be built without its dark-flat/bias
                    job_plan["calibrate_with"] = {dark.master["kind"].lower(): _ref(dark.master)}
                job_hash = plan_hash(job_plan)
                depends = [ref["job"] for ref in (job_plan.get("calibrate_with") or {}).values() if "job" in ref]
                plan.calib_jobs.append({"kind": "CALIB_MASTER", "plan": job_plan, "plan_hash": job_hash, "night": plan.night,
                                        "filter": master["filter"], "depends": depends,
                                        "scope": {"rig": plan.rig, "night": plan.night, "master_kind": kind}})
                pending.append({**master, "sha256": None, "pending_job": job_hash})
        return pending

    # ── light groups ─────────────────────────────────────────────────────
    def _light_groups(self, rig: str, lights: list[sqlite3.Row], projects: dict) -> list[LightGroup]:
        rig_cfg = self.config.rigs[rig]
        buckets = dict(zip([f["id"] for f in lights], matching.rotator_buckets([f["rotator_pos"] for f in lights], rig_cfg.rotator)))
        grouped: dict[tuple, list[sqlite3.Row]] = {}
        for f in lights:
            key = ("hub", f["hub_target_id"]) if f["hub_target_id"] is not None else ("text", f["target"])
            project = projects[key]
            gkey = (project["id"], f["filter"], round(f["exposure"] or 0, 2), f["gain"], f["offset"], f["binning"], f["readout_mode"],
                    f["width"], f["height"], buckets.get(f["id"]) if rig_cfg.rotator.present else None)
            grouped.setdefault(gkey, []).append(f)
        out = []
        for gkey, rows in sorted(grouped.items(), key=lambda kv: str(kv[0])):
            need = {"rig": rig, "camera": rows[0]["camera"], "filter": rows[0]["filter"], "binning": rows[0]["binning"], "gain": rows[0]["gain"],
                    "offset": rows[0]["offset"], "readout_mode": rows[0]["readout_mode"], "width": rows[0]["width"], "height": rows[0]["height"],
                    "exposure": rows[0]["exposure"], "sensor_temp": _median([r["sensor_temp"] for r in rows]),
                    "focal_length": _median([r["focal_length"] for r in rows]), "rotator_pos": gkey[-1],
                    "night": rows[0]["night"], "taken_at": rows[len(rows) // 2]["date_obs"]}
            out.append(LightGroup(gkey[0], rows[0]["filter"], rows, need))
        return out

    # ── stacks and the reference ─────────────────────────────────────────
    def _plan_stacks(self, plan: NightPlan, projects: dict[int, sqlite3.Row]) -> None:
        by_key: dict[tuple[int, str], list[LightGroup]] = {}
        for g in plan.groups:
            by_key.setdefault((g.project_id, g.filter), []).append(g)
        for group in plan.groups:
            for miss in group.misses:
                plan.issues.append(self._light_issue(plan, group, miss, projects[group.project_id]))
        references_planned: dict[int, dict] = {}
        for (project_id, filter_), groups in sorted(by_key.items()):
            project = projects[project_id]
            opts = settings(project, self.config)
            n_lights = sum(len(g.frames) for g in groups)
            if n_lights < int(opts.get("min_lights_per_stack") or 1):
                plan.skipped.append(f"{project_label(project)} {filter_}: {n_lights} light(s) < min_lights_per_stack")
                continue
            final = all(g.calibrated for g in groups)
            only_flats_missing = all(isinstance(g.dark, Match) for g in groups) and not final
            if not final and not (only_flats_missing and self.config.night_processing.produce_provisional_without_flat):
                continue
            stack_plan = {
                "kind": "NIGHT_STACK", "stack_kind": "final" if final else "provisional_noflat", "project_id": project_id,
                "project_path": project["path"], "rig": plan.rig, "night": plan.night, "filter": filter_,
                "reference_version": project["reference_version"], "drizzle_scale": int(opts.get("drizzle_scale") or 1),
                "wbpp_profile": opts.get("wbpp_profile"), "keep_calibrated_frames": bool(opts.get("keep_calibrated_frames", True)),
                "groups": [{"lights": sorted(f["sha256"] for f in g.frames), "rotator_pos": g.need["rotator_pos"], "exposure": g.need["exposure"],
                            "dark": _ref(g.dark.master), "flat": _ref(g.flat.master) if isinstance(g.flat, Match) else None,
                            "flat_evidence": g.flat.evidence if isinstance(g.flat, Match) else None} for g in groups],
                "input_paths": {f["sha256"]: f["logical_path"] for g in groups for f in g.frames},
            }
            depends: list[str] = []
            if final and not project["reference_sha256"]:
                ref = references_planned.get(project_id) or self._reference_job(plan, project, opts)
                if ref:
                    references_planned[project_id] = ref
                    depends.append(ref["plan_hash"])
                    stack_plan["reference"] = {"job": ref["plan_hash"]}
            elif project["reference_sha256"]:
                stack_plan["reference"] = {"sha256": project["reference_sha256"], "version": project["reference_version"]}
            depends += [g_ref["job"] for g in stack_plan["groups"] for g_ref in (g["dark"], g["flat"]) if g_ref and "job" in g_ref]
            plan.stacks.append({"kind": "NIGHT_STACK", "plan": stack_plan, "plan_hash": plan_hash(stack_plan), "night": plan.night,
                                "filter": filter_, "project_id": project_id, "depends": sorted(set(depends)),
                                "scope": {"rig": plan.rig, "night": plan.night, "filter": filter_, "project_id": project_id,
                                          "hub_target_id": project["hub_target_id"], "stack_kind": stack_plan["stack_kind"]}})
        plan.reference_jobs = [r for r in references_planned.values() if not r.get("existing")]

    def _reference_job(self, plan: NightPlan, project: sqlite3.Row, opts: dict) -> dict | None:
        """A PROJECT_REFERENCE from this night's final groups (§9.2), preferring
        the configured reference filter(s)."""
        finals = [g for g in plan.groups if g.project_id == project["id"] and g.calibrated]
        if not finals:
            return None
        known = self.catalog.one("SELECT plan_hash FROM jobs WHERE kind = 'PROJECT_REFERENCE' AND project_id = ? AND status IN "
                                 "('queued', 'staging', 'waiting_data', 'running') AND json_extract(plan_json, '$.version') = ?",
                                 (project["id"], project["reference_version"]))
        if known:   # already on its way: this night's stacks wait for it
            return {"plan_hash": known["plan_hash"], "existing": True}
        preferred = opts.get("reference_filter") or self.config.multi_night.reference_filter
        preferred = [preferred] if isinstance(preferred, str) else list(preferred)
        filters = sorted({g.filter for g in finals}, key=lambda f: (preferred.index(f) if f in preferred else len(preferred), f))
        ref_plan = {"kind": "PROJECT_REFERENCE", "project_id": project["id"], "project_path": project["path"], "rig": plan.rig,
                    "night": plan.night, "filters": filters, "version": project["reference_version"],
                    "groups": [{"filter": g.filter, "lights": sorted(f["sha256"] for f in g.frames), "dark": _ref(g.dark.master),
                                "flat": _ref(g.flat.master)} for g in finals],
                    "input_paths": {f["sha256"]: f["logical_path"] for g in finals for f in g.frames}}
        depends = sorted({ref["job"] for g in ref_plan["groups"] for ref in (g["dark"], g["flat"]) if "job" in ref})
        return {"kind": "PROJECT_REFERENCE", "plan": ref_plan, "plan_hash": plan_hash(ref_plan), "night": plan.night, "filter": None,
                "project_id": project["id"], "depends": depends,
                "scope": {"rig": plan.rig, "night": plan.night, "project_id": project["id"], "hub_target_id": project["hub_target_id"]}}

    # ── issues ───────────────────────────────────────────────────────────
    def _light_issue(self, plan: NightPlan, group: LightGroup, miss: Miss, project: sqlite3.Row) -> dict:
        rig_cfg = self.config.rigs[plan.rig]
        need = group.need
        n = len(group.frames)
        hours = sum((f["exposure"] or 0) for f in group.frames) / 3600
        if miss.kind in ("FLAT_MISSING", "ROTATOR_POSITION_UNKNOWN"):
            rot = f" at rotator position {need['rotator_pos']:g} {rig_cfg.rotator.units} (±{rig_cfg.rotator.tolerance:g})" if need["rotator_pos"] is not None else ""
            fingerprint = f"{miss.kind}:{plan.rig}:{need['filter']}:{need['rotator_pos']}:{need['binning']}:{plan.night}"
            fix = (f"To fix, take {need['filter']} flats{rot}, focal length {need['focal_length']:g} mm, bin {need['binning']}, before any "
                   "equipment change. The collector picks them up as usual; Altair builds the master flat, reprocesses the night and re-merges "
                   "automatically." if miss.kind == "FLAT_MISSING" else
                   "Fix the rotator keyword or the rig's rotator config, then `altair rerun`.")
        else:
            fingerprint = f"{miss.kind}:{plan.rig}:{need['gain']}:{need['offset']}:{need['binning']}:{need['exposure']}:{plan.night}"
            rot = ""
            fix = f"To fix, take matching {miss.kind.split('_')[0].lower()} frames ({need['exposure']:g}s, gain {need['gain']}, offset {need['offset']}, " \
                  f"bin {need['binning']}, around {need['sensor_temp']}°C). Altair builds the master and reruns automatically."
        message = (f"Night {plan.night} · {project_label(project)} · {rig_cfg.telescope} / {rig_cfg.camera} · filter {need['filter']}: "
                   f"{n} lights ({hours:.1f} h){rot}. {miss.reason[0].upper() + miss.reason[1:]}. {fix}")
        requirement = {"kind": {"FLAT_MISSING": "FLAT", "DARK_MISSING": "DARK", "ROTATOR_POSITION_UNKNOWN": "FLAT"}.get(miss.kind, miss.kind.split("_")[0]),
                       "need": need, "project_id": group.project_id}
        return {"kind": miss.kind, "fingerprint": fingerprint, "message": message, "requirement": requirement,
                "scope": {"rig": plan.rig, "night": plan.night, "filter": need["filter"], "project_id": group.project_id,
                          "hub_target_id": project["hub_target_id"], "frame_ids": [f["id"] for f in group.frames]}}

    def _calib_issue(self, plan: NightPlan, miss: Miss, master: dict, rows) -> dict:
        return {"kind": miss.kind, "fingerprint": f"{miss.kind}:{plan.rig}:flatgroup:{master['filter']}:{master['exposure']}:{plan.night}",
                "message": f"The {master['filter']} flats of {plan.night} on {plan.rig} can't be calibrated: {miss.reason}. Take matching "
                           f"{'dark-flats' if miss.kind == 'DARKFLAT_MISSING' else 'bias frames'}; Altair then builds the master flat.",
                "requirement": {"kind": miss.kind.split("_")[0], "need": {**master, "night": plan.night}},
                "scope": {"rig": plan.rig, "night": plan.night, "filter": master["filter"], "frame_ids": [r["id"] for r in rows]}}

    def _check_focal_length(self, plan: NightPlan, rig: str, night: str, lights) -> None:
        """A FOCALLEN change beyond tolerance since the previous night proposes a
        reducer_changed event; the user confirms it (§8.3)."""
        here = _median([f["focal_length"] for f in lights])
        prev = self.catalog.one("SELECT night FROM frames WHERE rig = ? AND night < ? AND image_type = 'light' ORDER BY night DESC LIMIT 1",
                                (rig, night))
        if here is None or prev is None:
            return
        before = _median([r["focal_length"] for r in self.catalog.query(
            "SELECT focal_length FROM frames WHERE rig = ? AND night = ? AND image_type = 'light'", (rig, prev["night"]))])
        tolerance = self.config.rigs[rig].focal_length_tolerance_mm
        if before is not None and abs(here - before) > tolerance:
            plan.issues.append({"kind": "EQUIPMENT_CHANGE_SUSPECTED", "severity": "warning",
                                "fingerprint": f"EQUIPMENT_CHANGE_SUSPECTED:{rig}:{night}",
                                "message": f"FOCALLEN on {rig} went from {before:g} mm ({prev['night']}) to {here:g} mm ({night}): was a reducer or "
                                           f"flattener changed? If so, `altair equipment log --rig {rig} reducer_changed --at <time>` so flats "
                                           "aren't matched across it.",
                                "requirement": None, "scope": {"rig": rig, "night": night}})

    # ── writing the plan ─────────────────────────────────────────────────
    def _commit(self, plan: NightPlan) -> None:
        with self.catalog.transaction() as tx:
            ids: dict[str, int] = {}
            for job in plan.jobs:
                ids[job["plan_hash"]] = self._insert_job(tx, job, ids, plan.rig)
            raised = set()
            for issue in plan.issues:
                raise_issue(tx, self.config, kind=issue["kind"], severity=issue.get("severity", "blocking"), fingerprint=issue["fingerprint"],
                            message=issue["message"], scope=issue["scope"], requirement=issue["requirement"])
                raised.add(issue["fingerprint"])
            self._resolve_stale_issues(tx, plan, raised)
            self._update_frame_status(tx, plan)

    def _insert_job(self, tx: sqlite3.Connection, job: dict, ids: dict[str, int], rig: str) -> int:
        return insert_job(tx, self.config, job, ids, rig)

    def _resolve_stale_issues(self, tx: sqlite3.Connection, plan: NightPlan, raised: set[str]) -> None:
        """Calibration issues for this rig-night that this plan no longer raises
        are resolved: a master now matches (auto-rerun, §10.4)."""
        kinds = ("FLAT_MISSING", "DARK_MISSING", "BIAS_MISSING", "DARKFLAT_MISSING", "ROTATOR_POSITION_UNKNOWN")
        for row in tx.execute(f"SELECT fingerprint, requirement_json FROM issues WHERE status = 'open' AND kind IN ({','.join('?' * len(kinds))}) "
                              f"AND fingerprint LIKE ?", (*kinds, f"%:{plan.rig}:%:{plan.night}")).fetchall():
            if row["fingerprint"] not in raised:
                resolve_issue(tx, self.config, row["fingerprint"], resolution="auto:matched")

    def _update_frame_status(self, tx: sqlite3.Connection, plan: NightPlan) -> None:
        """Lights of an uncalibrated group are held (linked to their issue);
        lights whose calibration now matches are released."""
        for group in plan.groups:
            if group.calibrated:
                status, reason = None, None
            else:
                status, reason = "held", "; ".join(f"{m.kind}" for m in group.misses)
            for f in group.frames:
                new_status = status or ("valid" if f["status"] == "held" else f["status"])
                if new_status != f["status"] or (reason or None) != (f["status_reason"] or None):
                    tx.execute("UPDATE frames SET status = ?, status_reason = ? WHERE id = ?", (new_status, reason, f["id"]))
                    if self.config.hub.enabled:
                        enqueue_frame_patch(tx, f["sha256"], {"status": new_status, "status_reason": reason})

    # ── plan requests (from the CLI and Hub commands) ────────────────────
    def process_requests(self) -> list[dict]:
        done = []
        for req in self.catalog.query("SELECT * FROM plan_requests WHERE done_at IS NULL ORDER BY id"):
            payload = json.loads(req["payload_json"])
            try:
                result = self._handle(req["kind"], payload)
            except Exception as exc:  # noqa: BLE001 - a bad request is recorded, never blocks the queue
                log.exception("plan request %s failed", req["id"])
                result = {"error": str(exc)}
            self.catalog.execute("UPDATE plan_requests SET done_at = ? WHERE id = ?", (now_iso(), req["id"]))
            done.append({"id": req["id"], "kind": req["kind"], **result})
        return done

    def _handle(self, kind: str, payload: dict) -> dict:
        if kind == "night_ready":
            self.plan_night(payload["rig"], payload["night"])
            return {"planned": [(payload["rig"], payload["night"])]}
        if kind in ("replan", "rerun", "equipment_event"):
            nights = self._nights_for(kind, payload)
            for rig, night in nights:
                self.plan_night(rig, night)
            return {"planned": nights}
        if kind in ("night_include", "night_exclude"):
            return self._decide_night(payload, "include" if kind == "night_include" else "exclude")
        if kind == "rereference":
            return self.rereference(payload["target_id"], payload.get("from_night"))
        if kind in ("approve_fetch", "deny_fetch"):
            self.catalog.set_state(f"fetch_decision:{payload['fingerprint']}", "approved" if kind == "approve_fetch" else "denied")
            return {"recorded": True}
        if kind == "set_mode":
            return {"merge": self._request_merges(self.catalog.query("SELECT id FROM projects WHERE hub_target_id = ?", (payload["target_id"],)))}
        return {"ignored": kind}

    def _nights_for(self, kind: str, payload: dict) -> list[tuple[str, str]]:
        if kind == "equipment_event":
            rows = self.catalog.query("SELECT DISTINCT json_extract(scope_json, '$.rig') AS rig, json_extract(scope_json, '$.night') AS night "
                                      "FROM issues WHERE status = 'open' AND kind IN ('FLAT_MISSING', 'ROTATOR_POSITION_UNKNOWN') "
                                      "AND json_extract(scope_json, '$.rig') = ?", (payload["rig"],))
            return [(r["rig"], r["night"]) for r in rows if r["night"]]
        if kind == "rerun" and payload.get("issue_id"):
            issue = self.catalog.one("SELECT scope_json FROM issues WHERE id = ? OR fingerprint = ?", (payload["issue_id"], str(payload["issue_id"])))
            scope = json.loads(issue["scope_json"]) if issue else {}
            return [(scope["rig"], scope["night"])] if scope.get("rig") and scope.get("night") else []
        clauses, args = ["image_type = 'light'"], []
        targets = payload.get("targets") or ([payload["target_id"]] if payload.get("target_id") else [])
        if targets:
            clauses.append(f"hub_target_id IN ({','.join('?' * len(targets))})")
            args += targets
        nights = payload.get("nights") or ([payload["night"]] if payload.get("night") else [])
        if nights:
            clauses.append(f"night IN ({','.join('?' * len(nights))})")
            args += nights
        if payload.get("filter"):
            clauses.append("filter = ?")
            args.append(payload["filter"])
        rows = self.catalog.query(f"SELECT DISTINCT rig, night FROM frames WHERE {' AND '.join(clauses)} ORDER BY night", tuple(args))
        return [(r["rig"], r["night"]) for r in rows if r["rig"] in self.config.rigs]

    def _decide_night(self, payload: dict, decision: str) -> dict:
        projects = self.catalog.query("SELECT id FROM projects WHERE hub_target_id = ?", (payload["target_id"],))
        with self.catalog.transaction() as tx:
            for p in projects:
                tx.execute("INSERT OR REPLACE INTO night_decisions(project_id, night, filter, decision, source, decided_at) VALUES (?, ?, ?, ?, 'hub', ?)",
                           (p["id"], payload["night"], payload["filter"], decision, now_iso()))
        return {"decision": decision, "merge": self._request_merges(projects, payload.get("filter"))}

    def _request_merges(self, projects, filter_: str | None = None) -> int:
        from altair.projects import merge as merger

        count = 0
        for p in projects:
            filters = [filter_] if filter_ else [r["filter"] for r in self.catalog.query(
                "SELECT DISTINCT filter FROM night_masters WHERE project_id = ?", (p["id"],))]
            for f in filters:
                count += bool(merger.plan_merge(self.catalog, self.config, p["id"], f))
        return count

    def rereference(self, target_id: int, from_night: str | None = None) -> dict:
        """A new reference (§9.7): bump the version; every night becomes
        STALE_REFERENCE and is reprocessed from its backed-up calibrated subs."""
        out = []
        for project in self.catalog.query("SELECT * FROM projects WHERE hub_target_id = ?", (target_id,)):
            with self.catalog.transaction() as tx:
                tx.execute("UPDATE projects SET reference_version = reference_version + 1, reference_sha256 = NULL, reference_night = NULL "
                           "WHERE id = ?", (project["id"],))
                raise_issue(tx, self.config, kind="STALE_REFERENCE", severity="blocking", fingerprint=f"STALE_REFERENCE:{project['id']}",
                            message=f"{project_label(project)} is being re-referenced (version {project['reference_version'] + 1}); its nights are "
                                    "reprocessed against the new reference and merged again.",
                            scope={"project_id": project["id"], "hub_target_id": target_id, "rig": project["rig"]})
            nights = [r["night"] for r in self.catalog.query("SELECT DISTINCT night FROM frames WHERE hub_target_id = ? AND rig = ? AND image_type = 'light' "
                                                            "AND night >= ? ORDER BY night", (target_id, project["rig"], from_night or ""))]
            if from_night and from_night in nights:
                nights = [from_night] + [n for n in nights if n != from_night]
            for night in nights:
                self.plan_night(project["rig"], night)
            out.append({"project_id": project["id"], "nights": nights})
        return {"rereferenced": out}
