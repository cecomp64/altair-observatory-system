"""`altair plan` and the processing commands (SPEC §12.1)."""
from __future__ import annotations

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


COMMANDS = [plan]
