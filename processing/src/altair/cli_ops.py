"""`altair issues`, `issue …`, `status`, `serve` and the local `doctor`
checks (SPEC §4.1, §10, §12.1)."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import click

from altair.cli_context import Ctx, check_line, pass_ctx


@click.command("issues")
@click.option("--open", "only_open", is_flag=True, help="Only open issues")
@click.option("--kind")
@pass_ctx
def issues(ctx: Ctx, only_open: bool, kind: str | None) -> None:
    """Issues, blocking first."""
    clauses, args = ["1 = 1"], []
    if only_open:
        clauses.append("status = 'open'")
    if kind:
        clauses.append("kind = ?")
        args.append(kind.upper())
    rows = ctx.catalog.query(f"SELECT * FROM issues WHERE {' AND '.join(clauses)} ORDER BY status = 'open' DESC, severity = 'blocking' DESC, id",
                             tuple(args))
    for row in rows:
        scope = json.loads(row["scope_json"])
        where = " ".join(str(x) for x in (scope.get("rig"), scope.get("night"), scope.get("filter")) if x)
        click.echo(f"#{row['id']:<5} {row['status']:<9} {row['severity']:<8} {row['kind']:<26} {where}")
    if not rows:
        click.echo("no issues")


@click.group()
def issue() -> None:
    """Show, resolve or waive an issue; the flats shopping list (SPEC §10.4)."""


@issue.command("show")
@click.argument("ref")
@pass_ctx
def issue_show(ctx: Ctx, ref: str) -> None:
    from altair.issue_actions import ActionError, find

    try:
        row = find(ctx.catalog, ref)
    except ActionError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"#{row['id']} {row['kind']} ({row['severity']}, {row['status']}) since {row['created_at']}")
    click.echo(row["message"])
    click.echo(f"scope: {row['scope_json']}")
    if row["requirement_json"]:
        click.echo(f"requirement: {row['requirement_json']}")
    if row["resolution"]:
        click.echo(f"resolution: {row['resolution']} at {row['resolved_at']}")


@issue.command("waive")
@click.argument("ref")
@click.option("--note", required=True)
@pass_ctx
def issue_waive(ctx: Ctx, ref: str, note: str) -> None:
    """Stop blocking and reminding; the nights stay out of the merge."""
    from altair.issue_actions import ActionError, waive

    try:
        result = waive(ctx.catalog, ctx.config, ref, note)
    except ActionError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"issue {result['waived']} waived" + (f"; {len(result['excluded'])} night(s) excluded from merging" if result["excluded"] else ""))


@issue.command("resolve")
@click.argument("ref")
@click.option("--flat", "flat", type=click.Path(exists=True, dir_okay=False), required=True, help="A master flat to apply")
@click.option("--force-match", is_flag=True, help="Apply it even though it doesn't match §8 (recorded as an override)")
@pass_ctx
def issue_resolve(ctx: Ctx, ref: str, flat: str, force_match: bool) -> None:
    from altair.issue_actions import ActionError, resolve_with_flat

    try:
        result = resolve_with_flat(ctx.catalog, ctx.config, ref, flat, force=force_match)
    except ActionError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"master flat {result['master_id']} registered{' as an override' if result['forced'] else ''}; the night is re-planned "
               "on the next `altair run`")


@issue.command("flats-plan")
@click.option("--format", "fmt", type=click.Choice(["md", "csv"]), default="md")
@click.option("--output", type=click.Path(dir_okay=False), help="Write to a file")
@pass_ctx
def issue_flats_plan(ctx: Ctx, fmt: str, output: str | None) -> None:
    """Which flats to take: rig, filter, rotator position, binning."""
    from altair.issue_actions import flats_plan

    text = flats_plan(ctx.catalog, fmt=fmt)
    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"written to {output}")
    else:
        click.echo(text, nl=False)


@click.command("status")
@click.option("--write", "write_page", is_flag=True, help="Also rewrite ALTAIR_STATUS.html/.json")
@pass_ctx
def status(ctx: Ctx, write_page: bool) -> None:
    """Open issues, projects, jobs and storage at a glance."""
    from altair import status_page

    s = status_page.build(ctx.catalog, ctx.config)
    click.echo(f"issues: {s['issues']['open']} open ({s['issues']['blocking']} blocking)")
    for need, items in s["issues"]["groups"].items():
        click.echo(f"  {need}: " + ", ".join(f"#{i['id']}" for i in items))
    for p in s["projects"]:
        merged = ", ".join(f"{m['filter']} v{m['version']} ({m['nights']} nights, {m['hours']} h)" for m in p["multi_night"]) or "no merge yet"
        click.echo(f"{p['label']} on {p['rig']}: {merged}")
        for x in p["excluded"]:
            click.echo(f"  not merged: {x['night']} {x['filter']} - {x['reason']}")
    click.echo("jobs: " + (", ".join(f"{n} {k}" for k, n in sorted(s["jobs"]["counts"].items())) or "none"))
    if write_page:
        html_path, _ = status_page.write(ctx.catalog, ctx.config)
        click.echo(f"status page: {html_path}")


@click.command("serve")
@click.option("--windowless", is_flag=True, help="Log to state/logs/altaird.log instead of the console")
@click.option("--once", is_flag=True, help="Run every worker once and exit")
@pass_ctx
def serve(ctx: Ctx, windowless: bool, once: bool) -> None:
    """altaird: every worker in one process (SPEC §4.1)."""
    import logging
    from logging.handlers import RotatingFileHandler

    from altair.daemon import Daemon

    handlers: list[logging.Handler]
    if windowless:
        ctx.config.paths.logs_dir.mkdir(parents=True, exist_ok=True)
        handlers = [RotatingFileHandler(ctx.config.paths.logs_dir / "altaird.log", maxBytes=20 << 20, backupCount=10, encoding="utf-8")]
    else:
        handlers = [logging.StreamHandler()]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", handlers=handlers)
    client = None
    if ctx.config.hub.enabled:
        try:
            client = ctx.client()
        except click.ClickException as exc:
            logging.getLogger("altaird").warning("Hub sync off: %s", exc.message)
    daemon = Daemon(ctx.catalog, ctx.config, s3=ctx.s3(required=False), hub_client=client)
    if once:
        for name, error in daemon.run_once().items():
            click.echo(f"{name}: {'ok' if error is None else error}")
        return
    daemon.serve_forever()


def local_checks(ctx: Ctx) -> bool:
    """Checks on this PC (SPEC §4.1); warnings, except an unwritable state folder."""
    from altair import winapi
    from altair.executor.pixinsight import runner_path

    cfg = ctx.config
    ok = True

    def warn(label: str, passed: bool, detail: str = "") -> None:
        click.echo(f"[{'ok' if passed else 'warn'}] {label}{': ' + detail if detail else ''}")

    state = Path(cfg.paths.state)
    try:
        state.mkdir(parents=True, exist_ok=True)
        probe = state / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        check_line("state folder writable", True, str(state))
    except OSError as exc:
        ok = check_line("state folder writable", False, f"{state}: {exc}")
    pi = Path(cfg.pixinsight.executable)
    warn("PixInsight executable", pi.exists(), str(pi))
    warn("PJSR runner", runner_path(cfg.pixinsight).exists(), str(runner_path(cfg.pixinsight)))
    for name, path in (("work", cfg.paths.work_dir), ("cache", cfg.cache_dir)):
        target = path if path.exists() else path.parent
        try:
            free = shutil.disk_usage(target).free / 1024 ** 3
            warn(f"{name} folder free space", free > 50, f"{path}: {free:.0f} GB free")
        except OSError as exc:
            warn(f"{name} folder", False, f"{path}: {exc}")
    if sys.platform == "win32":
        session = winapi.session_id()
        warn("interactive session (not a service)", session not in (None, 0), f"session {session}")
        warn("long paths enabled", bool(winapi.long_paths_enabled()), "HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled")
    for channel in cfg.notifications.channels:
        missing = [channel[k] for k in channel if k.endswith("_env") and channel[k] and not os.environ.get(channel[k])]
        warn(f"notification channel {channel.get('type')}", not missing, f"missing env {', '.join(missing)}" if missing else "")
    return ok


COMMANDS = [issues, issue, status, serve]
