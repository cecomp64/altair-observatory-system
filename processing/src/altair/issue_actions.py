"""Manual controls on issues (SPEC §10.4). The CLI and the Hub's commands
share them.

- ``waive``: the issue stops blocking and reminding; the nights it holds are
  excluded from merging for good (their masters stay provisional previews).
- ``resolve_with_flat``: apply a specific master flat. It is still checked
  against §8; ``force`` overrides a mismatch, which is recorded and shows as
  ``flat_override`` in the night's sidecar and on the status page.
- ``flats_plan``: the shopping list of flats to take.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.issues import resolve_issue
from altair.planner import matching

FLAT_KINDS = ("FLAT_MISSING", "ROTATOR_POSITION_UNKNOWN")


class ActionError(Exception):
    pass


def find(catalog: Catalog, ref: str | int):
    issue = catalog.one("SELECT * FROM issues WHERE id = ? OR fingerprint = ?", (ref, str(ref)))
    if issue is None:
        raise ActionError(f"no issue {ref}")
    return issue


def waive(catalog: Catalog, config: AltairConfig, ref: str | int, note: str, *, source: str = "cli") -> dict:
    issue = find(catalog, ref)
    if issue["status"] != "open":
        raise ActionError(f"issue {issue['id']} is {issue['status']}")
    scope = json.loads(issue["scope_json"])
    excluded = []
    with catalog.transaction() as tx:
        resolve_issue(tx, config, issue["fingerprint"], status="waived", resolution=f"waived:{note}")
        if scope.get("project_id") and scope.get("night") and scope.get("filter"):
            tx.execute("INSERT OR REPLACE INTO night_decisions(project_id, night, filter, decision, source, decided_at) VALUES (?, ?, ?, 'exclude', ?, ?)",
                       (scope["project_id"], scope["night"], scope["filter"], f"{source}:waive:{issue['id']}", now_iso()))
            excluded.append((scope["project_id"], scope["night"], scope["filter"]))
    if excluded:
        from altair.projects.merge import plan_merge

        for project_id, _, filter_ in excluded:
            plan_merge(catalog, config, project_id, filter_)
    return {"waived": issue["id"], "excluded": excluded}


def resolve_with_flat(catalog: Catalog, config: AltairConfig, ref: str | int, flat: str | Path, *, force: bool = False) -> dict:
    """Import ``flat`` as a master for the issue's rig and filter, check it
    against the requirement, and re-plan the night."""
    from altair.calibration import import_master

    issue = find(catalog, ref)
    if issue["kind"] not in FLAT_KINDS or not issue["requirement_json"]:
        raise ActionError(f"issue {issue['id']} ({issue['kind']}) isn't about a missing flat")
    need = json.loads(issue["requirement_json"])["need"]
    rig = config.rigs.get(need["rig"])
    if rig is None:
        raise ActionError(f"unknown rig {need['rig']}")
    master = import_master(catalog, config, flat, kind="FLAT", rig=need["rig"],
                           overrides={"filter": need["filter"], "binning": need.get("binning")})
    events = [dict(r) for r in catalog.query("SELECT * FROM equipment_events")]
    hit = matching.match_flat(need, [master], config.calibration_matching, rig.rotator, focal_tolerance=rig.focal_length_tolerance_mm,
                              events=events)
    if isinstance(hit, matching.Miss):
        if not force:
            raise ActionError(f"the flat doesn't match: {hit.reason}. Use --force-match to apply it anyway.")
        catalog.set_state(override_key(need["rig"], need["night"], need["filter"]),
                          {"sha256": master["sha256"], "issue_id": issue["id"], "reason": hit.reason, "at": now_iso()})
    with catalog.transaction() as tx:
        tx.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES ('rerun', ?, 'cli:issue_resolve', ?)",
                   (json.dumps({"issue_id": issue["id"]}), now_iso()))
    return {"master_id": master["id"], "sha256": master["sha256"], "forced": isinstance(hit, matching.Miss)}


def override_key(rig: str, night: str, filter_: str) -> str:
    return f"flat_override:{rig}:{night}:{filter_}"


def flats_plan(catalog: Catalog, *, fmt: str = "md") -> str:
    """Distinct (rig, filter, rotator position, binning, focal length) that open
    flat issues need, with the nights waiting on each."""
    wanted: dict[tuple, dict[str, Any]] = {}
    for issue in catalog.query("SELECT * FROM issues WHERE status = 'open' AND kind IN (?, ?) AND requirement_json IS NOT NULL", FLAT_KINDS):
        need = json.loads(issue["requirement_json"])["need"]
        key = (need["rig"], need["filter"], need.get("rotator_pos"), need.get("binning"), need.get("focal_length"))
        entry = wanted.setdefault(key, {"nights": set(), "issues": []})
        entry["nights"].add(need["night"])
        entry["issues"].append(issue["id"])
    rows = [{"rig": k[0], "filter": k[1], "rotator_pos": k[2], "binning": k[3], "focal_length_mm": k[4],
             "nights": ", ".join(sorted(v["nights"])), "issues": ", ".join(f"#{i}" for i in v["issues"])} for k, v in sorted(wanted.items(), key=str)]
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=["rig", "filter", "rotator_pos", "binning", "focal_length_mm", "nights", "issues"])
        writer.writeheader()
        writer.writerows(rows)
        return out.getvalue()
    lines = ["| Rig | Filter | Rotator | Bin | Focal length | Nights waiting | Issues |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['rig']} | {r['filter']} | {r['rotator_pos'] if r['rotator_pos'] is not None else '—'} | {r['binning']} | "
              f"{r['focal_length_mm'] or '—'} | {r['nights']} | {r['issues']} |" for r in rows]
    return "\n".join(lines) + "\n" if rows else "No flats needed.\n"
