"""The status page (SPEC §10.3): ``ALTAIR_STATUS.html`` (static, rewritten
after every job) and ``ALTAIR_STATUS.json`` beside it, at ``issues.page``
(default: the state folder).

It shows:
- open issues grouped by what they need (a shopping list of flats and darks);
- each project's multi-night master, with per-night weights and contributions;
- excluded nights with their reasons;
- the job queue and storage health.
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from altair import __version__
from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.planner.projects import project_label

NEEDS = {"FLAT_MISSING": "Flats to take", "ROTATOR_POSITION_UNKNOWN": "Flats to take", "DARK_MISSING": "Darks to take",
         "DARKFLAT_MISSING": "Dark-flats to take", "BIAS_MISSING": "Bias frames to take"}


def page_paths(config: AltairConfig) -> tuple[Path, Path]:
    html_path = Path(config.issues.page) if config.issues.page else Path(config.paths.state) / "ALTAIR_STATUS.html"
    return html_path, html_path.with_suffix(".json")


def build(catalog: Catalog, config: AltairConfig) -> dict[str, Any]:
    issues = [dict(r) for r in catalog.query("SELECT * FROM issues WHERE status = 'open' ORDER BY severity = 'blocking' DESC, id")]
    groups: dict[str, list[dict]] = {}
    for issue in issues:
        scope = json.loads(issue["scope_json"])
        item = {"id": issue["id"], "kind": issue["kind"], "severity": issue["severity"], "message": issue["message"],
                "rig": scope.get("rig"), "night": scope.get("night"), "filter": scope.get("filter"), "since": issue["created_at"]}
        groups.setdefault(NEEDS.get(issue["kind"], "Other issues"), []).append(item)
    projects = []
    for p in catalog.query("SELECT * FROM projects ORDER BY id"):
        latest = catalog.query("SELECT * FROM multi_night_masters m WHERE project_id = ? AND version = (SELECT max(version) FROM "
                               "multi_night_masters WHERE project_id = m.project_id AND filter = m.filter) ORDER BY filter", (p["id"],))
        nights = catalog.query("SELECT night, filter, kind, merge_status, merge_block_reason, n_frames, total_exposure_s FROM night_masters "
                               "WHERE project_id = ? AND superseded_by IS NULL ORDER BY night, filter", (p["id"],))
        projects.append({
            "id": p["id"], "label": project_label(p), "hub_target_id": p["hub_target_id"], "rig": p["rig"],
            "reference_version": p["reference_version"], "reference_night": p["reference_night"],
            "multi_night": [{"filter": m["filter"], "version": m["version"], "nights": m["n_nights"],
                             "hours": round((m["total_exposure_s"] or 0) / 3600, 2), "created_at": m["created_at"],
                             "inputs": [{k: i.get(k) for k in ("night", "weight", "percent", "frames")} for i in json.loads(m["inputs_json"])],
                             "excluded": json.loads(m["excluded_json"])} for m in latest],
            "nights": [dict(n) for n in nights],
            "excluded": [{"night": n["night"], "filter": n["filter"], "reason": n["merge_block_reason"]} for n in nights
                         if n["merge_status"] == "blocked"],
        })
    jobs = {r["status"]: r["n"] for r in catalog.query("SELECT status, count(*) AS n FROM jobs GROUP BY status")}
    running = [dict(r) for r in catalog.query("SELECT id, kind, rig, night, filter, status, waiting_reason FROM jobs "
                                              "WHERE status IN ('running', 'staging', 'waiting_data') ORDER BY id")]
    health = catalog.get_state("nas_health")
    backup = catalog.one("SELECT count(*) AS n FROM blobs b WHERE data_class = 'raw_light' AND NOT EXISTS (SELECT 1 FROM replicas r "
                         "WHERE r.sha256 = b.sha256 AND r.location = 's3' AND r.state IN ('present', 'archived_cold', 'restored'))")["n"]
    return {"generated_at": now_iso(), "altair_version": __version__, "site": config.site.name,
            "issues": {"open": len(issues), "blocking": sum(i["severity"] == "blocking" for i in issues), "groups": groups},
            "projects": projects, "jobs": {"counts": jobs, "active": running},
            "storage": {"nas": health, "raw_lights_without_s3": backup}}


def render_html(status: dict[str, Any]) -> str:
    e = html.escape
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
             f"<meta http-equiv='refresh' content='300'><title>Altair status</title><style>{CSS}</style></head><body>",
             f"<h1>Altair · {e(status['site'])}</h1><p class='muted'>Updated {e(status['generated_at'])} · Altair {e(status['altair_version'])}</p>"]
    iss = status["issues"]
    parts.append(f"<h2>Open issues <span class='badge{' bad' if iss['blocking'] else ''}'>{iss['open']}</span></h2>")
    if not iss["groups"]:
        parts.append("<p>Nothing needs attention.</p>")
    for need, items in iss["groups"].items():
        parts.append(f"<h3>{e(need)}</h3><ul>")
        for i in items:
            where = " · ".join(str(x) for x in (i["rig"], i["night"], i["filter"]) if x)
            parts.append(f"<li class='{e(i['severity'])}'><b>#{i['id']} {e(i['kind'])}</b> {e(where)}<br><span>{e(i['message'])}</span></li>")
        parts.append("</ul>")
    parts.append("<h2>Projects</h2>")
    for p in status["projects"]:
        parts.append(f"<h3>{e(p['label'])} <span class='muted'>{e(p['rig'] or '')} · reference v{p['reference_version']}"
                     f"{' from ' + e(p['reference_night']) if p['reference_night'] else ''}</span></h3>")
        for m in p["multi_night"]:
            rows = "".join(f"<tr><td>{e(i['night'])}</td><td>{i['frames'] or ''}</td><td>{(i['percent'] or 0):.1f}%</td></tr>" for i in m["inputs"])
            parts.append(f"<p><b>{e(m['filter'])}</b> v{m['version']}: {m['nights']} night(s), {m['hours']} h</p>"
                         f"<table><tr><th>Night</th><th>Frames</th><th>Contribution</th></tr>{rows}</table>")
        if p["excluded"]:
            parts.append("<p>Not merged:</p><ul>" + "".join(f"<li>{e(x['night'])} {e(x['filter'])}: {e(x['reason'] or '')}</li>" for x in p["excluded"]) + "</ul>")
    jobs = status["jobs"]
    parts.append("<h2>Jobs</h2><p>" + (", ".join(f"{n} {e(s)}" for s, n in sorted(jobs["counts"].items())) or "none") + "</p>")
    if jobs["active"]:
        parts.append("<ul>" + "".join(f"<li>#{j['id']} {e(j['kind'])} {e(j['night'] or '')} {e(j['filter'] or '')}: {e(j['status'])}"
                                      f"{' – ' + e(j['waiting_reason']) if j['waiting_reason'] else ''}</li>" for j in jobs["active"]) + "</ul>")
    nas = status["storage"]["nas"] or {}
    state = "ok" if nas.get("healthy") and nas.get("reachable") else e(str(nas.get("reason") or "unknown"))
    free = f" · {nas['free_percent']:.0f}% free" if nas.get("free_percent") is not None else ""
    parts.append(f"<h2>Storage</h2><p>NAS: {state}{free} · raw lights waiting for S3: {status['storage']['raw_lights_without_s3']}</p>")
    parts.append("</body></html>")
    return "".join(parts)


CSS = ("body{font:15px/1.45 system-ui,sans-serif;max-width:60rem;margin:1rem auto;padding:0 1rem;color:#1d2330;background:#fff}"
       "@media(prefers-color-scheme:dark){body{background:#12151c;color:#dde3ee}th,td{border-color:#333a48!important}}"
       "h1{font-size:1.4rem}h2{font-size:1.15rem;margin-top:1.6rem}.muted{color:#7a8496;font-weight:400}"
       "li{margin:.4rem 0}li.blocking b{color:#c0392b}li.warning b{color:#b7791f}"
       ".badge{background:#7a8496;color:#fff;border-radius:1rem;padding:0 .5rem;font-size:.85rem}.badge.bad{background:#c0392b}"
       "table{border-collapse:collapse}th,td{border-bottom:1px solid #e3e6ec;padding:.2rem .8rem;text-align:left}")


def write(catalog: Catalog, config: AltairConfig) -> tuple[Path, Path]:
    status = build(catalog, config)
    html_path, json_path = page_paths(config)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    for path, text in ((json_path, json.dumps(status, indent=1, default=str)), (html_path, render_html(status))):
        partial = path.with_name(path.name + ".partial")
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, path)
    return html_path, json_path
