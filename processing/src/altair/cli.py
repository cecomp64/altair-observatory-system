"""`altair` CLI (SPEC §12.1), the Hub-integration commands of v0.8."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from altair import __version__
from altair import frames as frame_ops
from altair.cli_context import DEFAULT_CONFIG, Ctx, pass_ctx
from altair.config import AltairConfig


@click.group()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True, envvar="ALTAIR_CONFIG", help="Path to altair.yaml")
@click.version_option(__version__)
@click.pass_context
def main(ctx: click.Context, config_path: str) -> None:
    """Altair: automated pre-processing, reporting to the Hub."""
    ctx.obj = Ctx(config_path)


# ── hub ──────────────────────────────────────────────────────────────────
@main.group()
def hub() -> None:
    """Hub connection: status, sync, reconciliation, the outbox."""


@hub.command("status")
@pass_ctx
def hub_status(ctx: Ctx) -> None:
    """Reachability, last config/command poll, outbox depth, parked items."""
    cat = ctx.catalog
    from altair.hub.config_sync import load_cached

    cached = load_cached(cat)
    rows = {
        "hub": ctx.config.hub.base_url if ctx.config.hub.enabled else "disabled",
        "node": ctx.config.hub.node,
        "last contact": cat.get_state("last_hub_contact_at") or "never",
        "unreachable since": cat.get_state("hub_unreachable_since") or "—",
        "last config pull": cat.get_state("last_config_pull_at") or "never",
        "last command poll": cat.get_state("last_command_poll_at") or "never",
        "cached targets": len(cached.targets) if cached else 0,
        "outbox pending": cat.one("SELECT count(*) AS n FROM hub_outbox WHERE sent_at IS NULL AND parked = 0")["n"],
        "outbox parked": cat.one("SELECT count(*) AS n FROM hub_outbox WHERE sent_at IS NULL AND parked = 1")["n"],
        "open HUB_* issues": cat.one("SELECT count(*) AS n FROM issues WHERE status = 'open' AND kind LIKE 'HUB_%'")["n"],
    }
    for key, value in rows.items():
        click.echo(f"{key:>20}: {value}")


@hub.command("sync-now")
@pass_ctx
def hub_sync_now(ctx: Ctx) -> None:
    """Pull config and commands, drain the outbox, send a heartbeat."""
    click.echo(json.dumps(ctx.sync().sync_now(), default=str, indent=2))


@hub.command("pull-config")
@pass_ctx
def hub_pull_config(ctx: Ctx) -> None:
    config = ctx.sync().pull_config()
    click.echo(f"api_revision {config.api_revision}: {len(config.telescopes)} telescopes, {len(config.targets)} targets")


@hub.command("reconcile")
@click.option("--night")
@click.option("--rig")
@pass_ctx
def hub_reconcile(ctx: Ctx, night: str | None, rig: str | None) -> None:
    """Compare nightly digests with the Hub; re-queue a night's frames on a mismatch."""
    from altair.hub.reconcile import reconcile

    report = reconcile(ctx.catalog, ctx.config, ctx.client(), rig=rig, night=night)
    for entry in report:
        outcome = "ok" if entry["ok"] else f"MISMATCH, re-queued {entry['requeued']} frames"
        click.echo(f"{entry['rig']} {entry['night']}: {outcome}")
    if not report:
        click.echo("No closed nights to reconcile.")


@hub.group("outbox")
def hub_outbox() -> None:
    """Inspect and manage queued Hub reports."""


@hub_outbox.command("list")
@click.option("--parked", is_flag=True)
@pass_ctx
def outbox_list(ctx: Ctx, parked: bool) -> None:
    rows = ctx.catalog.query("SELECT id, kind, natural_key, attempts, last_error FROM hub_outbox WHERE sent_at IS NULL AND parked = ? ORDER BY id LIMIT 200",
                             (int(parked),))
    for row in rows:
        click.echo(f"{row['id']:>7} {row['kind']:<18} {row['natural_key'][:40]:<40} attempts {row['attempts']} {row['last_error'] or ''}")


@hub_outbox.command("retry")
@click.argument("item_id", type=int)
@pass_ctx
def outbox_retry(ctx: Ctx, item_id: int) -> None:
    ctx.catalog.execute("UPDATE hub_outbox SET parked = 0, attempts = 0, next_attempt_at = NULL WHERE id = ?", (item_id,))
    click.echo(f"Item {item_id} will be retried on the next drain.")


@hub_outbox.command("drop")
@click.argument("item_id", type=int)
@pass_ctx
def outbox_drop(ctx: Ctx, item_id: int) -> None:
    ctx.catalog.execute("UPDATE hub_outbox SET sent_at = datetime('now'), last_error = 'dropped by operator' WHERE id = ? AND sent_at IS NULL", (item_id,))
    click.echo(f"Item {item_id} dropped.")


# ── frames ───────────────────────────────────────────────────────────────
@main.group()
def frames() -> None:
    """Unlinked frames and manual assignment."""


@frames.command("unlinked")
@click.option("--night")
@click.option("--rig")
@pass_ctx
def frames_unlinked(ctx: Ctx, night: str | None, rig: str | None) -> None:
    """What PROJECT_UNRESOLVED is holding, grouped by rig, night and OBJECT."""
    sql = ("SELECT rig, night, coalesce(target, '') AS object, count(*) AS n FROM frames "
           "WHERE image_type = 'light' AND hub_target_id IS NULL")
    params: list[str] = []
    for column, value in (("night", night), ("rig", rig)):
        if value:
            sql += f" AND {column} = ?"
            params.append(value)
    for row in ctx.catalog.query(sql + " GROUP BY rig, night, object ORDER BY night DESC", tuple(params)):
        click.echo(f"{row['rig']:<20} {row['night']} {row['object']!r:<30} {row['n']} lights")


@frames.command("assign")
@click.option("--target", "target_id", type=int, required=True, help="Hub target id")
@click.option("--sha256", "sha256s", multiple=True)
@click.option("--night")
@click.option("--rig")
@click.option("--object", "object_header")
@pass_ctx
def frames_assign(ctx: Ctx, target_id: int, sha256s: tuple[str, ...], night: str | None, rig: str | None, object_header: str | None) -> None:
    """Assign frames to a Hub target (manual; the resolution rules never override it)."""
    if not sha256s and not (night and rig):
        raise click.UsageError("give --sha256 ... or --night and --rig (and --object)")
    count = frame_ops.assign(ctx.catalog, ctx.config, target_id=target_id, sha256s=list(sha256s) or None,
                             rig=rig, night=night, object_header=object_header)
    click.echo(f"Assigned {count} frames to target {target_id}.")


# ── projects ─────────────────────────────────────────────────────────────
@main.group()
def project() -> None:
    """Processing projects and their Hub ids."""


@project.command("list")
@click.option("--hub-project", type=int)
@pass_ctx
def project_list(ctx: Ctx, hub_project: int | None) -> None:
    """Hub targets on this node's rigs, with frame counts."""
    from altair.hub.config_sync import load_cached

    cached = load_cached(ctx.catalog)
    if not cached:
        raise click.ClickException("No Hub config cached yet: run `altair hub pull-config`.")
    counts = {r["hub_target_id"]: r["n"] for r in ctx.catalog.query(
        "SELECT hub_target_id, count(*) AS n FROM frames WHERE image_type = 'light' AND hub_target_id IS NOT NULL GROUP BY hub_target_id")}
    for target in sorted(cached.targets.values(), key=lambda t: t["id"]):
        if hub_project and target["project_id"] != hub_project:
            continue
        click.echo(f"T{target['id']:<6} P{target['project_id']:<6} {target['name']:<30} {target['telescope']}/{target.get('optical_train') or '*'} "
                   f"{target['status']:<12} {counts.get(target['id'], 0)} lights")


@project.command("show")
@click.option("--target", "target_id", type=int, required=True)
@pass_ctx
def project_show(ctx: Ctx, target_id: int) -> None:
    from altair.hub.config_sync import load_cached

    cached = load_cached(ctx.catalog)
    target = cached.targets.get(target_id) if cached else None
    if not target:
        raise click.ClickException(f"Target {target_id} isn't in the cached Hub config.")
    click.echo(json.dumps(target, indent=2))
    for row in ctx.catalog.query("SELECT night, filter, count(*) AS n, sum(exposure) AS s FROM frames WHERE hub_target_id = ? AND image_type = 'light' "
                                 "GROUP BY night, filter ORDER BY night", (target_id,)):
        click.echo(f"  {row['night']} {row['filter']}: {row['n']} lights, {round((row['s'] or 0) / 3600, 2)} h")


# ── index ────────────────────────────────────────────────────────────────
@main.command("index")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--rig", required=True)
@click.option("--adopt", is_flag=True, help="Copy files into the NAS layout so they can be processed")
@click.option("--dry-run", is_flag=True)
@click.option("--nas-root", help="Defaults to storage.locations[nas].root in altair.yaml")
@pass_ctx
def index_cmd(ctx: Ctx, directory: Path, rig: str, adopt: bool, dry_run: bool, nas_root: str | None) -> None:
    """Catalogue existing FITS/XISF files from RIG in place, read-only."""
    from altair.hub.config_sync import load_cached
    from altair.index.indexer import index

    nas_root = nas_root or _nas_root(ctx.config)
    report = index(ctx.catalog, ctx.config, directory, rig=rig, hub_config=load_cached(ctx.catalog), nas_root=nas_root,
                   adopt=adopt, dry_run=dry_run)
    click.echo(f"seen {report.seen}, indexed {report.indexed}, already known {report.already_known}, linked {report.linked}, "
               f"unlinked {report.unlinked}, other rig {report.skipped_unknown_rig}, errors {len(report.errors)}")
    for error in report.errors[:20]:
        click.echo(f"  {error}", err=True)
    if ctx.config.hub.enabled and not dry_run:
        click.echo("Frames are queued for the Hub; `altair hub sync-now` sends them now.")


def _nas_root(config: AltairConfig) -> str | None:
    return config.storage.nas.root if config.storage.nas else None


# ── doctor ───────────────────────────────────────────────────────────────
@main.command()
@pass_ctx
def doctor(ctx: Ctx) -> None:
    """This PC (state, PixInsight, disks, session) and the Hub (SPEC §4.1, §5.1): reachability, key scopes, node,
    optical trains, timezone, filters."""
    from observatory_contracts import check_hub_revision

    from altair.hub.client import HubError
    from altair.hub.sync import mismatches

    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        click.echo(f"[{'ok' if passed else 'FAIL'}] {label}{': ' + detail if detail else ''}")

    from altair.cli_ops import local_checks

    ok = local_checks(ctx)
    if not ctx.config.hub.enabled:
        click.echo("hub.enabled is false: standalone mode, no Hub checks.")
        sys.exit(0 if ok else 1)
    try:
        sync = ctx.sync()
        hub_config = sync.pull_config()
        check("Hub reachable, key accepted", True, ctx.config.hub.base_url)
        check("node name matches", hub_config.raw["node"]["name"] == ctx.config.hub.node, hub_config.raw["node"]["name"])
        check("Hub API revision compatible", *check_hub_revision(hub_config.api_revision))
        for scope_check in ("commands", "heartbeat"):
            try:
                sync.commands.ack_unacked() if scope_check == "commands" else sync.heartbeat()
                check(f"key allows {scope_check}", True)
            except HubError as exc:
                check(f"key allows {scope_check}", False, str(exc))
        for name, rig in ctx.config.rigs.items():
            if not rig.hub:
                continue
            problems = mismatches(ctx.config, name, hub_config)
            check(f"rig {name} matches {rig.hub.telescope}/{rig.hub.optical_train}", not problems, "; ".join(problems))
            seen = {r["filter"] for r in ctx.catalog.query("SELECT DISTINCT filter FROM frames WHERE rig = ? AND filter IS NOT NULL ORDER BY night DESC LIMIT 50", (name,))}
            unknown = sorted(f for f in seen if not hub_config.canonical_filter(rig.hub.telescope, rig.hub.optical_train, f))
            check(f"rig {name} filters known to the Hub", not unknown, ", ".join(unknown))
    except HubError as exc:
        check("Hub reachable", False, str(exc))
    sys.exit(0 if ok else 1)


@main.command("serve-hub")
@click.option("--interval", default=5.0, show_default=True)
@pass_ctx
def serve_hub(ctx: Ctx, interval: float) -> None:
    """Run the hub_sync loop in the foreground (altaird runs it as a thread)."""
    ctx.sync().run_forever(interval)


from altair import cli_ops, cli_processing, cli_storage  # noqa: E402 - command families live in their own modules

for command in cli_storage.COMMANDS + cli_processing.COMMANDS + cli_ops.COMMANDS:
    main.add_command(command)


if __name__ == "__main__":
    main()
