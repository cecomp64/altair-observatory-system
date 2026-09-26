"""`altair plan` and the processing commands (SPEC §12.1)."""
from __future__ import annotations

import json

import click

from altair.cli_context import Ctx, pass_ctx


@click.command("plan")
@click.option("--night", required=True)
@click.option("--rig", help="Default: every rig with frames that night")
@pass_ctx
def plan(ctx: Ctx, night: str, rig: str | None) -> None:
    """Dry run: groups, calibration matches, stacks and the issues it would raise."""
    from altair.planner.matching import Match
    from altair.planner.plan import Planner

    rigs = [rig] if rig else [r["rig"] for r in ctx.catalog.query("SELECT DISTINCT rig FROM frames WHERE night = ?", (night,)) if r["rig"] in ctx.config.rigs]
    planner = Planner(ctx.catalog, ctx.config, ctx.hub_config)
    for name in rigs:
        result = planner.plan_night(name, night, dry_run=True)
        click.echo(f"{name} {night}")
        for job in result.calib_jobs:
            m = job["plan"]["master"]
            click.echo(f"  build master {m['kind']} {m.get('filter') or ''} {m['exposure']}s from {m['n_frames']} sub(s)")
        for g in result.groups:
            dark = "dark ok" if isinstance(g.dark, Match) else f"{g.dark.kind}: {g.dark.reason}"
            flat = "flat ok" if isinstance(g.flat, Match) else f"{g.flat.kind}: {g.flat.reason}"
            click.echo(f"  {len(g.frames)} {g.filter} light(s) {g.need['exposure']}s rot {g.need['rotator_pos']}: {dark}; {flat}")
        for s in result.reference_jobs + result.stacks:
            p = s["plan"]
            click.echo(f"  {s['kind']} {p.get('stack_kind', '')} project {p['project_id']} {p.get('filter') or ', '.join(p.get('filters', []))}")
        for issue in result.issues:
            click.echo(f"  issue {issue['kind']}: {issue['message']}")
        for note in result.skipped:
            click.echo(f"  skipped: {note}")


def _executor(ctx: Ctx):
    from altair.executor.executor import Executor
    from altair.storage.stager import Stager

    return Executor(ctx.catalog, ctx.config, stager=Stager(ctx.catalog, ctx.config, s3=ctx.s3(required=False)))


def _request(ctx: Ctx, kind: str, payload: dict, source: str = "cli") -> None:
    from altair.catalog.db import now_iso

    ctx.catalog.execute("INSERT INTO plan_requests(kind, payload_json, source, created_at) VALUES (?, ?, ?, ?)",
                        (kind, json.dumps(payload), source, now_iso()))


def _process_requests(ctx: Ctx) -> None:
    from altair.planner.plan import Planner

    for done in Planner(ctx.catalog, ctx.config, ctx.hub_config).process_requests():
        click.echo(f"request {done['id']} {done['kind']}: {', '.join(f'{k}={v}' for k, v in done.items() if k not in ('id', 'kind'))}")


@click.command("run")
@click.option("--max-jobs", type=int, help="Stop after this many jobs")
@pass_ctx
def run(ctx: Ctx, max_jobs: int | None) -> None:
    """Process pending plan requests, then run every runnable job through PixInsight."""
    _process_requests(ctx)
    executor = _executor(ctx)
    recovered = executor.recover()
    if recovered:
        click.echo(f"recovered job(s) {', '.join(map(str, recovered))} after a restart")
    for report in executor.run_all(max_jobs=max_jobs):
        click.echo(f"job {report.job_id} {report.kind}: {report.status}{' - ' + report.detail if report.detail else ''}")
    click.echo(f"{executor.pending()} job(s) still pending")


@click.command("jobs")
@click.option("--status", "statuses", multiple=True, help="Only these statuses (repeatable)")
@click.option("--limit", type=int, default=50)
@pass_ctx
def jobs(ctx: Ctx, statuses: tuple[str, ...], limit: int) -> None:
    """The job queue, newest first."""
    clause = f"WHERE status IN ({','.join('?' * len(statuses))})" if statuses else ""
    for row in ctx.catalog.query(f"SELECT * FROM jobs {clause} ORDER BY id DESC LIMIT ?", (*statuses, limit)):
        scope = json.loads(row["scope_json"])
        what = " ".join(str(x) for x in (row["rig"], row["night"], row["filter"], scope.get("master_kind"), scope.get("stack_kind")) if x)
        extra = row["waiting_reason"] or row["error"] or ""
        click.echo(f"{row['id']:>5} {row['kind']:<17} {row['status']:<12} {what}{'  - ' + extra if extra else ''}")


@click.command("rerun")
@click.option("--job", "job_id", type=int, help="Run this job again")
@click.option("--issue", "issue_id", help="Re-plan the night of this issue")
@click.option("--night")
@click.option("--target", "target_id", type=int, help="Hub target id")
@click.option("--filter", "filter_")
@pass_ctx
def rerun(ctx: Ctx, job_id: int | None, issue_id: str | None, night: str | None, target_id: int | None, filter_: str | None) -> None:
    """Run a job again, or re-plan nights (after fixing headers, rigs or calibration)."""
    if job_id is not None:
        if not _executor(ctx).rerun(job_id):
            raise click.ClickException(f"job {job_id} doesn't exist or is running")
        click.echo(f"job {job_id} queued")
        return
    if not (issue_id or night or target_id):
        raise click.ClickException("give --job, --issue, --night or --target")
    _request(ctx, "rerun", {"issue_id": issue_id, "night": night, "target_id": target_id, "filter": filter_})
    _process_requests(ctx)


@click.group()
def calib() -> None:
    """The calibration library (SPEC §8)."""


@calib.command("list")
@click.option("--kind", type=click.Choice(["bias", "dark", "darkflat", "flat"], case_sensitive=False))
@click.option("--rig")
@click.option("--all", "include_superseded", is_flag=True, help="Include superseded masters")
@pass_ctx
def calib_list(ctx: Ctx, kind: str | None, rig: str | None, include_superseded: bool) -> None:
    from altair.calibration import library

    for m in library(ctx.catalog, kind=kind, rig=rig, include_superseded=include_superseded):
        detail = " ".join(str(x) for x in (m["filter"], f"{m['exposure']:g}s" if m["exposure"] is not None else None,
                                            f"{m['sensor_temp']:g}C" if m["sensor_temp"] is not None else None,
                                            f"rot {m['rotator_pos']:g}" if m["rotator_pos"] is not None else None) if x)
        flags = (" imported" if m["imported"] else "") + (" superseded" if m["superseded_by"] else "")
        click.echo(f"{m['id']:>5} {m['rig']:<12} {m['kind']:<9} {m['night']} g{m['gain']} o{m['offset']} bin{m['binning']} {detail} "
                   f"n={m['n_frames']} {m['sha256'][:12]}{flags}")


@calib.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--kind", type=click.Choice(["bias", "dark", "darkflat", "flat"], case_sensitive=False), required=True)
@click.option("--rig", required=True)
@click.option("--night", help="Default: from DATE-OBS")
@click.option("--filter", "filter_")
@click.option("--exposure", type=float)
@click.option("--gain", type=int)
@click.option("--offset", type=int)
@click.option("--temp", "sensor_temp", type=float)
@click.option("--binning")
@click.option("--rotator", "rotator_pos", type=float)
@pass_ctx
def calib_import(ctx: Ctx, path: str, kind: str, rig: str, **overrides) -> None:
    """Add a master made elsewhere to the library; header values unless overridden."""
    from altair.calibration import ImportError_, import_master

    try:
        master = import_master(ctx.catalog, ctx.config, path, kind=kind, rig=rig, overrides=overrides)
    except ImportError_ as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"imported {master['kind']} {master['night']} as {master['logical_path']}")
    if master["reruns_queued"]:
        click.echo(f"it resolves issue(s) {', '.join(map(str, master['reruns_queued']))}: reruns queued (`altair run`)")


@click.group()
def night() -> None:
    """Merge decisions for a night (SPEC §9.4)."""


def _decide(ctx: Ctx, kind: str, target_id: int, night_: str, filter_: str) -> None:
    _request(ctx, kind, {"target_id": target_id, "night": night_, "filter": filter_})
    _process_requests(ctx)


@night.command("include")
@click.option("--target", "target_id", type=int, required=True)
@click.option("--night", "night_", required=True)
@click.option("--filter", "filter_", required=True)
@pass_ctx
def night_include(ctx: Ctx, target_id: int, night_: str, filter_: str) -> None:
    """Merge the night despite a QUALITY_OUTLIER warning."""
    _decide(ctx, "night_include", target_id, night_, filter_)


@night.command("exclude")
@click.option("--target", "target_id", type=int, required=True)
@click.option("--night", "night_", required=True)
@click.option("--filter", "filter_", required=True)
@pass_ctx
def night_exclude(ctx: Ctx, target_id: int, night_: str, filter_: str) -> None:
    """Keep the night out of the merge."""
    _decide(ctx, "night_exclude", target_id, night_, filter_)


@click.command("merge")
@click.option("--target", "target_id", type=int, required=True)
@click.option("--filter", "filter_", required=True)
@click.option("--dry-run", is_flag=True, help="Show the gates and the nights that would go in")
@pass_ctx
def merge(ctx: Ctx, target_id: int, filter_: str, dry_run: bool) -> None:
    """Queue a multi-night merge (normally automatic after each night)."""
    from altair.projects import merge as merger

    projects = ctx.catalog.query("SELECT * FROM projects WHERE hub_target_id = ?", (target_id,))
    if not projects:
        raise click.ClickException(f"no project for target {target_id}")
    for project in projects:
        for gate in merger.evaluate(ctx.catalog, ctx.config, project, filter_):
            nm = gate.night_master
            click.echo(f"  {nm['night']} {filter_}: {'eligible' if gate.eligible else 'blocked - ' + gate.reason}")
        job = merger.plan_merge(ctx.catalog, ctx.config, project["id"], filter_, dry_run=dry_run)
        if job is None:
            click.echo(f"project {project['id']}: nothing to merge")
        else:
            click.echo(f"project {project['id']}: MERGE of {len(job['plan']['nights'])} night(s) {'would be ' if dry_run else ''}queued")


@click.command("publish")
@click.option("--refresh", is_flag=True, required=True, help="Re-create the viewing copies under paths.published")
@pass_ctx
def publish(ctx: Ctx, refresh: bool) -> None:
    """Re-create the published viewing copies from the canonical masters."""
    from altair.publish.publisher import Publisher

    publisher = Publisher(ctx.catalog, ctx.config)
    click.echo(f"{publisher.refresh()} viewing copies written; {publisher.backfill_nas()} output(s) written to the NAS")


COMMANDS = [plan, run, jobs, rerun, calib, night, merge, publish]
