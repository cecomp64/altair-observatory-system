"""Merge eligibility and MERGE planning (SPEC §9.4, §9.5).

`plan_merge` evaluates every current final night master of (project,
filter) against the gates, records ``merge_status`` and the block reason,
raises or resolves the gate issues, and queues a MERGE job over the eligible
nights. The merge is always rebuilt from all eligible nights, so its plan
(and ``plan_hash``) depends only on which masters go in: a plan that already
succeeded is not run again.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import dataclass
from typing import Any

from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.issues import raise_issue, resolve_issue
from altair.planner.projects import project_label, settings

GATE_ISSUES = ("SCALE_MISMATCH", "LOW_OVERLAP", "QUALITY_OUTLIER")


@dataclass
class Gate:
    night_master: dict[str, Any]
    eligible: bool
    reason: str | None = None
    issue: str | None = None          # kind of the issue that explains the block
    severity: str = "blocking"


def _metrics(row: dict) -> dict:
    return json.loads(row.get("metrics_json") or "{}")


def evaluate(catalog: Catalog, config: AltairConfig, project: sqlite3.Row, filter_: str) -> list[Gate]:
    opts = settings(project, config)
    rows = [dict(r) for r in catalog.query("SELECT * FROM night_masters WHERE project_id = ? AND filter = ? AND kind = 'final' "
                                           "AND superseded_by IS NULL ORDER BY night", (project["id"], filter_))]
    decisions = {r["night"]: r["decision"] for r in catalog.query("SELECT night, decision FROM night_decisions WHERE project_id = ? AND filter = ?",
                                                                  (project["id"], filter_))}
    fwhms = [_metrics(r).get("fwhm") for r in rows if r["reference_version"] == project["reference_version"]]
    fwhms = [f for f in fwhms if f]
    median_fwhm = statistics.median(fwhms) if fwhms else None
    ratio = opts.get("max_fwhm_ratio_to_project_median", config.multi_night.max_fwhm_ratio_to_project_median)
    # Calibration issues opened after a night was stacked (e.g. an equipment event
    # logged later invalidates its flat) keep it out until it is reprocessed.
    open_calib = {}
    for issue in catalog.query("SELECT kind, scope_json FROM issues WHERE status = 'open' AND kind IN "
                               "('FLAT_MISSING', 'ROTATOR_POSITION_UNKNOWN', 'DARK_MISSING', 'BIAS_MISSING', 'DARKFLAT_MISSING')"):
        scope = json.loads(issue["scope_json"])
        if scope.get("project_id") == project["id"] and scope.get("filter") == filter_:
            open_calib[scope.get("night")] = issue["kind"]
    gates = []
    for row in rows:
        m = _metrics(row)
        calib = json.loads(row.get("calib_json") or "{}")
        decision = decisions.get(row["night"])
        if decision == "exclude":
            gates.append(Gate(row, False, "excluded by the user"))
        elif row["reference_version"] != project["reference_version"]:
            gates.append(Gate(row, False, f"registered to reference v{row['reference_version']}, the project is at "
                                          f"v{project['reference_version']}", "STALE_REFERENCE"))
        elif not row["flat_verified"]:
            gates.append(Gate(row, False, "no verified flat", "FLAT_MISSING"))
        elif row["night"] in open_calib:
            gates.append(Gate(row, False, f"its calibration no longer matches ({open_calib[row['night']]})", open_calib[row["night"]]))
        elif int(calib.get("drizzle_scale") or 1) != int(project["drizzle_scale"] or 1):
            gates.append(Gate(row, False, f"drizzle scale {calib.get('drizzle_scale')} vs the project's {project['drizzle_scale']}",
                              "SCALE_MISMATCH"))
        elif m.get("overlap_fraction") is not None and m["overlap_fraction"] < config.multi_night.min_overlap_fraction:
            gates.append(Gate(row, False, f"overlap with the reference {m['overlap_fraction']:.0%} < "
                                          f"{config.multi_night.min_overlap_fraction:.0%}", "LOW_OVERLAP"))
        elif (ratio and median_fwhm and m.get("fwhm") and m["fwhm"] > ratio * median_fwhm and decision != "include"):
            gates.append(Gate(row, False, f"FWHM {m['fwhm']:.2f} > {ratio:g} × the project median {median_fwhm:.2f}",
                              "QUALITY_OUTLIER", "warning"))
        else:
            gates.append(Gate(row, True))
    return gates


def plan_merge(catalog: Catalog, config: AltairConfig, project_id: int, filter_: str, *, dry_run: bool = False) -> dict | None:
    """Record the gates and queue a MERGE; returns the job (dict with
    ``plan_hash``) or None when nothing needs merging."""
    from altair.planner.plan import insert_job

    project = catalog.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if project is None:
        return None
    opts = settings(project, config)
    gates = evaluate(catalog, config, project, filter_)
    if not dry_run:
        _record(catalog, config, project, filter_, gates)
    eligible = [g.night_master for g in gates if g.eligible]
    mn = opts.get("multi_night") or {}
    if not mn.get("enabled", config.multi_night.enabled) or not project["reference_sha256"]:
        return None
    if len(eligible) < max(config.multi_night.min_nights, 1):
        return None
    job = build(catalog, config, project, filter_, eligible, [g for g in gates if not g.eligible])
    done = catalog.one("SELECT status FROM jobs WHERE plan_hash = ?", (job["plan_hash"],))
    if done and done["status"] in ("succeeded", "queued", "staging", "waiting_data", "running"):
        return None
    if dry_run:
        return job
    with catalog.transaction() as tx:
        insert_job(tx, config, job, {}, project["rig"])
    return job


def build(catalog: Catalog, config: AltairConfig, project: sqlite3.Row, filter_: str, eligible: list[dict], blocked: list[Gate]) -> dict:
    from altair.planner.plan import plan_hash

    opts = settings(project, config)
    mode = project["multi_night_mode"] or (opts.get("multi_night") or {}).get("mode") or config.multi_night.mode   # see projects.set_mode
    mn = config.multi_night
    nights = []
    input_paths: dict[str, str] = {}
    frames: list[str] = []
    for row in eligible:
        m = _metrics(row)
        used = json.loads(row["input_frames_json"] or "[]")
        weight_sum = sum((json.loads(q["quality_json"] or "{}").get("psf_signal_weight") or 0)
                         for q in catalog.query(f"SELECT quality_json FROM frames WHERE sha256 IN ({','.join('?' * len(used))})", tuple(used))) \
            if used else 0
        nights.append({"id": row["id"], "night": row["night"], "sha256": row["sha256"], "frames": row["n_frames"],
                       "exposure_s": row["total_exposure_s"], "fwhm": m.get("fwhm"), "frame_weight_sum": weight_sum or None,
                       "frame_sha256s": used})
        input_paths[row["sha256"]] = _logical(catalog, row["sha256"])
        if mode == "frame_reintegration":
            for sha in json.loads(row["calib_json"] or "{}").get("calibrated", []):
                frames.append(sha)
                input_paths[sha] = _logical(catalog, sha)
    input_paths[project["reference_sha256"]] = _logical(catalog, project["reference_sha256"])
    rejection = mn.rejection if len(nights) >= 8 else "none"
    plan = {"kind": "MERGE", "project_id": project["id"], "project_path": project["path"], "rig": project["rig"], "filter": filter_,
            "reference_version": project["reference_version"], "reference": {"sha256": project["reference_sha256"]},
            "mode": mode, "weighting": mn.night_weighting, "normalization": mn.normalization, "rejection": rejection,
            "min_coverage_nights": mn.min_coverage_nights, "autocrop": mn.autocrop_output, "drizzle_scale": project["drizzle_scale"],
            "nights": nights, "calibrated_frames": sorted(frames), "input_paths": input_paths,
            "excluded": [{"night": g.night_master["night"], "reason": g.reason, "issue": g.issue} for g in blocked]}
    # The plan's identity is what goes in; exclusion reasons don't change the result.
    identity = {k: v for k, v in plan.items() if k != "excluded"}
    return {"kind": "MERGE", "plan": plan, "plan_hash": plan_hash(identity), "night": None, "filter": filter_, "project_id": project["id"],
            "depends": [], "scope": {"rig": project["rig"], "project_id": project["id"], "filter": filter_,
                                     "hub_target_id": project["hub_target_id"], "nights": [n["night"] for n in nights]}}


def _logical(catalog: Catalog, sha: str) -> str:
    row = catalog.one("SELECT logical_path FROM blobs WHERE sha256 = ?", (sha,))
    return row["logical_path"] if row else sha


def _record(catalog: Catalog, config: AltairConfig, project: sqlite3.Row, filter_: str, gates: list[Gate]) -> None:
    label = project_label(project)
    with catalog.transaction() as tx:
        for g in gates:
            row = g.night_master
            status = "eligible" if g.eligible else "blocked"
            if row["merge_status"] != "merged" or not g.eligible:
                tx.execute("UPDATE night_masters SET merge_status = ?, merge_block_reason = ? WHERE id = ?", (status, g.reason, row["id"]))
            for kind in GATE_ISSUES:
                fingerprint = f"{kind}:{project['id']}:{row['night']}:{filter_}"
                if g.issue == kind:
                    fix = {"QUALITY_OUTLIER": f"`altair night include {label} {row['night']} {filter_}` merges it anyway.",
                           "LOW_OVERLAP": "Check the framing of that night; `altair night exclude` keeps it out for good.",
                           "SCALE_MISMATCH": "Reprocess the night with the project's drizzle scale (`altair rerun`)."}[kind]
                    raise_issue(tx, config, kind=kind, severity=g.severity, fingerprint=fingerprint,
                                message=f"{label} {filter_} night {row['night']} is not merged: {g.reason}. {fix}",
                                scope={"project_id": project["id"], "hub_target_id": project["hub_target_id"], "night": row["night"],
                                       "filter": filter_, "rig": project["rig"]})
                else:
                    resolve_issue(tx, config, fingerprint, resolution="auto:gate_passed")
        stale = tx.execute("SELECT 1 FROM night_masters WHERE project_id = ? AND kind = 'final' AND superseded_by IS NULL "
                           "AND reference_version != ?", (project["id"], project["reference_version"])).fetchone()
        if project["reference_sha256"] and not stale:
            resolve_issue(tx, config, f"STALE_REFERENCE:{project['id']}", resolution="auto:reprocessed")
