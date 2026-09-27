"""Per-night and per-merge reports (SPEC §13), in Markdown.

- **Night report:** frames used and rejected with their weights, FWHM and
  eccentricity; the calibration used with its match evidence (exposure and
  temperature Δ for the dark; night, age and rotator Δ for the flat; flat
  overrides); timings; the night's open issues.
- **Merge report:** nights with weights and % contribution, excluded nights
  with their reasons, the combination settings, and a coverage map preview.

Each report is written twice:
- next to the viewing copies under ``paths.published``, as
  ``<target>/<night>/report_<filter>.md`` or
  ``<target>/multinight/<filter>_vNNN_report.md``, with the coverage image;
- into the cache as ``reports/<kind>_<id>.md`` without images, from where it
  goes to the Hub with the data product, and the Hub renders it.

Reports are regenerated from the catalog and the sidecars, so
`altair publish --refresh` can rewrite them at any time.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.planner.projects import project_label
from altair.storage import blobs

log = logging.getLogger("altair.reports")


# ── Markdown helpers ─────────────────────────────────────────────────────
def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        value = f"{value:.3g}" if abs(value) < 1000 else f"{value:.0f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list[Any]], align: str | None = None) -> str:
    """A GFM table; ``align`` is one letter per column (l / r / c)."""
    marks = {"l": ":---", "r": "---:", "c": ":---:"}
    spec = [marks[a] for a in (align or "l" * len(headers))]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(spec) + " |"]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def _hours(seconds: float | None) -> str:
    return f"{(seconds or 0) / 3600:.2f} h"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes} min {secs:02d} s" if minutes else f"{secs} s"


def report_path(config: AltairConfig, kind: str, altair_id: int) -> Path:
    return config.cache_dir / "reports" / f"{kind}_{altair_id}.md"


# ── inputs ───────────────────────────────────────────────────────────────
def _read_blob_json(catalog: Catalog, sha: str | None) -> dict | None:
    if not sha:
        return None
    for location in ("cache", "nas"):
        row = blobs.replicas(catalog.conn, sha).get(location)
        if row and row["state"] == "present" and Path(row["uri"]).exists():
            try:
                return json.loads(Path(row["uri"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return None


def _master(catalog: Catalog, ref: dict | None) -> dict | None:
    """A plan's master ref: by SHA-256, or by the job that built it."""
    if ref and not ref.get("sha256") and ref.get("job"):
        job = catalog.one("SELECT result_json FROM jobs WHERE plan_hash = ?", (ref["job"],))
        sha = (json.loads(job["result_json"] or "{}").get("registered") or {}).get("master") if job else None
        ref = {"sha256": sha} if sha else None
    if not ref or not ref.get("sha256"):
        return None
    row = catalog.one("SELECT * FROM calibration_masters WHERE sha256 = ?", (ref["sha256"],))
    return dict(row) if row else {"sha256": ref["sha256"]}


def _master_label(m: dict | None) -> str:
    if not m:
        return "—"
    if "kind" not in m:
        return m["sha256"][:12]
    parts = [m["kind"].lower(), m.get("night")]
    if m.get("exposure") is not None and m["kind"] != "BIAS":
        parts.append(f"{m['exposure']:g} s")
    if m.get("sensor_temp") is not None and m["kind"] == "DARK":
        parts.append(f"{m['sensor_temp']:g} °C")
    if m.get("rotator_pos") is not None:
        parts.append(f"rotator {m['rotator_pos']:g}")
    parts.append(f"{m.get('n_frames') or '?'} subs")
    return " · ".join(str(p) for p in parts if p) + (" (imported)" if m.get("imported") else "")


def _job(catalog: Catalog, job_id: int | None) -> dict | None:
    row = catalog.one("SELECT * FROM jobs WHERE id = ?", (job_id,)) if job_id else None
    return dict(row) if row else None


# ── the night report ─────────────────────────────────────────────────────
def night_markdown(catalog: Catalog, config: AltairConfig, night_master_id: int) -> str:
    nm = dict(catalog.one("SELECT * FROM night_masters WHERE id = ?", (night_master_id,)))
    project = catalog.one("SELECT * FROM projects WHERE id = ?", (nm["project_id"],))
    calib = json.loads(nm["calib_json"] or "{}")
    metrics = json.loads(nm["metrics_json"] or "{}")
    sidecar = _read_blob_json(catalog, calib.get("sidecar")) or {}
    rig = config.rigs.get(project["rig"] or "")
    kind = "Final" if nm["kind"] == "final" else "Provisional (no flat)"
    out = [f"# {project_label(project)} · {nm['filter']} · night {nm['night']}", ""]
    out.append(f"{kind} night master on **{project['rig']}**"
               + (f" ({rig.telescope} / {rig.camera})" if rig else "")
               + f", registered to reference v{nm['reference_version']}. Merge status: **{nm['merge_status']}**"
               + (f" — {nm['merge_block_reason']}" if nm["merge_block_reason"] else "") + ".")
    out += ["", "## Summary", ""]
    frames = sidecar.get("frames") or []
    n_all = len(sidecar.get("inputs") or []) or (nm["n_frames"] or 0) + (nm["n_rejected"] or 0)
    out.append(table(["", ""], [
        ["Frames used", f"{nm['n_frames']} of {n_all}"],
        ["Rejected", nm["n_rejected"]],
        ["Integration", _hours(nm["total_exposure_s"])],
        ["Median FWHM", metrics.get("fwhm")],
        ["Median eccentricity", metrics.get("eccentricity")],
        ["Overlap with the reference", f"{metrics['overlap_fraction']:.0%}" if metrics.get("overlap_fraction") is not None else None],
        ["Drizzle", f"{calib.get('drizzle_scale') or 1}×"],
        ["Engine", metrics.get("engine")],
        ["Master", f"`{nm['sha256'][:16]}…`"],
    ], "lr"))

    out += ["", "## Calibration", ""]
    rows = []
    for i, g in enumerate(calib.get("groups") or []):
        dark, flat = _master(catalog, g.get("dark")), _master(catalog, g.get("flat"))
        de, fe = g.get("dark_evidence") or {}, g.get("flat_evidence") or {}
        dark_note = f"Δt {de['temp_delta']:+g} °C" if de.get("temp_delta") is not None else ""
        flat_note = []
        if fe.get("age_days") is not None:
            flat_note.append(f"{abs(fe['age_days'])} d {'before' if fe['age_days'] > 0 else 'after' if fe['age_days'] < 0 else 'same night'}")
        if fe.get("rotator_delta") is not None:
            flat_note.append(f"rotator Δ {fe['rotator_delta']:g}")
        if fe.get("flat_override"):
            flat_note.append(f"**override** (issue #{fe.get('issue_id')}: {fe.get('override_reason')})")
        rows.append([i + 1, f"{g.get('exposure') or 0:g} s", g.get("rotator_pos"), _master_label(dark), dark_note,
                     _master_label(flat) if flat else "none (provisional)", "; ".join(flat_note)])
    out.append(table(["Group", "Exposure", "Rotator", "Dark", "Dark match", "Flat", "Flat match"], rows, "lrrllll")
               if rows else "No calibration recorded.")

    out += ["", "## Frames", ""]
    names = {r["sha256"]: r["file_name"] for r in catalog.query(
        f"SELECT sha256, file_name FROM frames WHERE sha256 IN ({','.join('?' * len(frames))})", tuple(f["sha256"] for f in frames))} if frames else {}
    frame_rows = []
    for f in sorted(frames, key=lambda f: names.get(f["sha256"]) or f["sha256"]):
        frame_rows.append([names.get(f["sha256"]) or f["sha256"][:12], "used" if f.get("used", True) else f"rejected: {f.get('reason') or ''}",
                           f.get("weight"), f.get("fwhm"), f.get("eccentricity"), f.get("stars")])
    out.append(table(["Frame", "Result", "Weight", "FWHM", "Eccentricity", "Stars"], frame_rows, "llrrrr")
               if frame_rows else "Per-frame results are not available (the sidecar isn't readable here).")

    job = _job(catalog, nm["job_id"])
    if job:
        timings = json.loads(job["result_json"] or "{}").get("timings") or {}
        out += ["", "## Timings", "", table(["", ""], [
            ["Queued", job["created_at"]], ["Started", job["started_at"]], ["Finished", job["finished_at"]],
            ["Staging", _duration(timings.get("staging_s"))], ["PixInsight", _duration(timings.get("run_s"))],
            ["Attempts", job["attempts"]]], "lr")]

    issues = [i for i in catalog.query("SELECT * FROM issues WHERE status = 'open' ORDER BY id")
              if (s := json.loads(i["scope_json"])).get("night") == nm["night"] and s.get("rig") == project["rig"]
              and s.get("filter") in (None, nm["filter"])]
    out += ["", "## Open issues", ""]
    out += [f"- **#{i['id']} {i['kind']}** ({i['severity']}): {i['message']}" for i in issues] or ["None."]
    return "\n".join(out) + "\n"


# ── the merge report ─────────────────────────────────────────────────────
def merge_markdown(catalog: Catalog, config: AltairConfig, mnm_id: int, *, coverage_image: str | None = None) -> str:
    m = dict(catalog.one("SELECT * FROM multi_night_masters WHERE id = ?", (mnm_id,)))
    project = catalog.one("SELECT * FROM projects WHERE id = ?", (m["project_id"],))
    job = catalog.one("SELECT * FROM jobs WHERE plan_hash = ?", (m["plan_hash"],))
    plan = json.loads(job["plan_json"]) if job else {}
    inputs = json.loads(m["inputs_json"])
    excluded = json.loads(m["excluded_json"] or "[]")
    fwhm = {r["id"]: json.loads(r["metrics_json"] or "{}").get("fwhm") for r in catalog.query(
        f"SELECT id, metrics_json FROM night_masters WHERE id IN ({','.join('?' * len(inputs))})",
        tuple(i.get("night_master_id") for i in inputs))} if inputs else {}
    out = [f"# {project_label(project)} · {m['filter']} · multi-night master v{m['version']}", ""]
    out.append(f"{m['n_nights']} night(s), {_hours(m['total_exposure_s'])} of integration, built {m['created_at']}.")
    out += ["", "## Settings", "", table(["", ""], [
        ["Mode", plan.get("mode")], ["Night weighting", plan.get("weighting")], ["Normalization", plan.get("normalization")],
        ["Rejection", plan.get("rejection")], ["Reference", f"v{plan.get('reference_version')}" if plan.get("reference_version") else None],
        ["Autocrop", f"to pixels covered by {plan.get('min_coverage_nights')} night(s)" if plan.get("autocrop") else "off"],
        ["Drizzle", f"{plan.get('drizzle_scale') or 1}×"]], "lr")]
    out += ["", "## Nights", ""]
    rows = [[i["night"], i.get("frames"), _hours(i.get("exposure_s")), fwhm.get(i.get("night_master_id")), i.get("weight"),
             f"{i.get('percent') or 0:.1f} %"] for i in sorted(inputs, key=lambda i: i["night"])]
    out.append(table(["Night", "Frames", "Integration", "FWHM", "Weight", "Contribution"], rows, "lrrrrr"))
    out += ["", "## Excluded nights", ""]
    out += [f"- **{x['night']}**: {x.get('reason') or 'excluded'}" + (f" ({x['issue']})" if x.get("issue") else "") for x in excluded] or ["None."]
    if coverage_image:
        out += ["", "## Coverage", "", f"![Coverage: nights per pixel]({coverage_image})"]
    return "\n".join(out) + "\n"


# ── writing ──────────────────────────────────────────────────────────────
def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="utf-8", newline="\n")
    partial.replace(path)
    return path


def write_night(catalog: Catalog, config: AltairConfig, night_master_id: int) -> list[Path]:
    nm = catalog.one("SELECT * FROM night_masters WHERE id = ?", (night_master_id,))
    project = catalog.one("SELECT * FROM projects WHERE id = ?", (nm["project_id"],))
    text = night_markdown(catalog, config, night_master_id)
    kind = "night_master" if nm["kind"] == "final" else "provisional_noflat"
    written = [_write(report_path(config, kind, night_master_id), text)]
    if config.paths.published:
        written.append(_write(Path(config.paths.published) / project_label(project) / nm["night"] / f"report_{nm['filter']}.md", text))
    return written


def write_merge(catalog: Catalog, config: AltairConfig, mnm_id: int) -> list[Path]:
    m = catalog.one("SELECT * FROM multi_night_masters WHERE id = ?", (mnm_id,))
    project = catalog.one("SELECT * FROM projects WHERE id = ?", (m["project_id"],))
    written = [_write(report_path(config, "multi_night_master", mnm_id), merge_markdown(catalog, config, mnm_id))]
    if config.paths.published:
        folder = Path(config.paths.published) / project_label(project) / "multinight"
        stem = f"{m['filter']}_v{m['version']:03d}"
        image = _coverage_preview(catalog, config, m, folder / f"{stem}_coverage.jpg")
        written.append(_write(folder / f"{stem}_report.md",
                              merge_markdown(catalog, config, mnm_id, coverage_image=image.name if image else None)))
    return written


def _coverage_preview(catalog: Catalog, config: AltairConfig, m, dest: Path) -> Path | None:
    job = catalog.one("SELECT result_json FROM jobs WHERE plan_hash = ?", (m["plan_hash"],))
    sha = (json.loads(job["result_json"] or "{}").get("registered") or {}).get("coverage") if job else None
    source = None
    for location in ("cache", "nas"):
        row = blobs.replicas(catalog.conn, sha).get(location) if sha else None
        if row and row["state"] == "present" and Path(row["uri"]).exists():
            source = row["uri"]
            break
    if source is None:
        return None
    from altair.hub.previews import render

    try:
        preview, thumb = render(source, dest, dest.with_name(dest.stem + "_thumb.jpg"), long_edge=1024, thumb=256)
    except Exception as exc:  # noqa: BLE001 - a missing image never stops the report
        log.warning("no coverage preview for %s: %s", dest, exc)
        return None
    thumb.unlink(missing_ok=True)
    return preview


def for_job(catalog: Catalog, config: AltairConfig, job_id: int) -> list[Path]:
    """The reports a finished job produces, then its data product is reported
    to the Hub again with the report attached."""
    from altair.publish import products

    job = catalog.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if job is None or job["status"] != "succeeded":
        return []
    written: list[Path] = []
    if job["kind"] == "NIGHT_STACK":
        for nm in catalog.query("SELECT id, kind FROM night_masters WHERE job_id = ?", (job_id,)):
            written += write_night(catalog, config, nm["id"])
            with catalog.transaction() as tx:
                products.enqueue(tx, config, "night_master" if nm["kind"] == "final" else "provisional_noflat", nm["id"])
    elif job["kind"] == "MERGE":
        for m in catalog.query("SELECT id, inputs_json FROM multi_night_masters WHERE plan_hash = ?", (job["plan_hash"],)):
            written += write_merge(catalog, config, m["id"])
            with catalog.transaction() as tx:
                products.enqueue(tx, config, "multi_night_master", m["id"])
            # The merged nights' reports now show them as merged.
            for night_master_id in {i.get("night_master_id") for i in json.loads(m["inputs_json"])} - {None}:
                written += write_night(catalog, config, night_master_id)
                with catalog.transaction() as tx:
                    products.enqueue(tx, config, "night_master", night_master_id)
    return written


def refresh_all(catalog: Catalog, config: AltairConfig) -> int:
    count = 0
    for nm in catalog.query("SELECT id FROM night_masters WHERE superseded_by IS NULL"):
        count += bool(write_night(catalog, config, nm["id"]))
    for m in catalog.query("SELECT id FROM multi_night_masters"):
        count += bool(write_merge(catalog, config, m["id"]))
    return count
