"""`altair rigs`, `altair collect` and `altair storage` (SPEC §12.1): the
collector, NAS, S3 backup, cleanup and catalog backups."""
from __future__ import annotations

import json
import sys

import click

from altair.cli_context import Ctx, check_line, human_bytes, pass_ctx


# ── rigs ─────────────────────────────────────────────────────────────────
@click.group()
def rigs() -> None:
    """The rigs Altair collects from."""


@rigs.command("list")
@pass_ctx
def rigs_list(ctx: Ctx) -> None:
    for name, rig in ctx.config.rigs.items():
        hub = f" -> {rig.hub.telescope}/{rig.hub.optical_train}" if rig.hub else ""
        click.echo(f"{name}: {rig.telescope}/{rig.camera} {rig.focal_length_mm:g} mm, raw root {rig.raw_root or '-'}{hub}")


@rigs.command("check")
@click.option("--rig")
@pass_ctx
def rigs_check(ctx: Ctx, rig: str | None) -> None:
    """Reachability, read/delete permission and free space of each rig share."""
    import os

    from altair.storage.locations import rig_location

    ok = True
    for name in [rig] if rig else list(ctx.config.rigs):
        source = rig_location(ctx.config, name)
        if source is None:
            click.echo(f"{name}: no raw_root (not collected)")
            continue
        reachable = source.reachable()
        ok &= check_line(f"{name} reachable", reachable, str(source.root))
        if reachable:
            ok &= check_line(f"{name} readable", os.access(source.root, os.R_OK))
            cleanup = ctx.config.rig_cleanup(name).enabled
            check_line(f"{name} deletable (cleanup)", os.access(source.root, os.W_OK) or not cleanup,
                       "cleanup disabled" if not cleanup else "")
            free = source.free_percent()
            check_line(f"{name} free space", free is None or free > 10, f"{free:.1f}%" if free is not None else "unknown")
    sys.exit(0 if ok else 1)


# ── collect ──────────────────────────────────────────────────────────────
@click.group()
def collect() -> None:
    """The collector: status, pulling now, closing a night, excluding a file."""


def _collector(ctx: Ctx, rig: str):
    from altair.collector.rig_worker import RigCollector

    if rig not in ctx.config.rigs:
        raise click.ClickException(f"unknown rig {rig!r}")
    return RigCollector(ctx.catalog, ctx.config, rig, hub_config=ctx.hub_config)


@collect.command("status")
@click.option("--rig")
@pass_ctx
def collect_status(ctx: Ctx, rig: str | None) -> None:
    for name in [rig] if rig else list(ctx.config.rigs):
        counts = {r["state"]: r["n"] for r in ctx.catalog.query("SELECT state, count(*) AS n FROM rig_files WHERE rig = ? GROUP BY state", (name,))}
        click.echo(f"{name}: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing seen yet"))
        for night in ctx.catalog.query("SELECT * FROM collections WHERE rig = ? AND state != 'closed' ORDER BY night", (name,)):
            click.echo(f"  night {night['night']}: {night['state']} (last frame {night['last_frame_at'] or '-'})")
        for stuck in ctx.catalog.query("SELECT rel_path, last_error FROM rig_files WHERE rig = ? AND state = 'stuck' LIMIT 20", (name,)):
            click.echo(f"  stuck: {stuck['rel_path']}: {stuck['last_error']}")


@collect.command("now")
@click.option("--rig", required=True)
@pass_ctx
def collect_now(ctx: Ctx, rig: str) -> None:
    report = _collector(ctx, rig).poll()
    click.echo(f"{rig}: reachable {report.reachable}, NAS usable {report.nas_usable}, seen {report.seen}, collected {report.collected}, "
               f"known {report.already_known}, closed {', '.join(report.closed) or '-'}")
    for failure in report.failed:
        click.echo(f"  {failure}", err=True)


@collect.command("close-night")
@click.option("--rig", required=True)
@click.option("--night", required=True)
@pass_ctx
def collect_close_night(ctx: Ctx, rig: str, night: str) -> None:
    from altair import nights

    state = nights.request_close(ctx.catalog, ctx.config, rig=rig, night=night, closed_by="manual")
    if state == "closing":
        _collector(ctx, rig).poll()
        state = ctx.catalog.one("SELECT state FROM collections WHERE rig = ? AND night = ?", (rig, night))["state"]
    click.echo(f"{rig} {night}: {state}")


@collect.command("exclude")
@click.option("--rig", required=True)
@click.argument("rel_path")
@pass_ctx
def collect_exclude(ctx: Ctx, rig: str, rel_path: str) -> None:
    """Stop waiting for a file that will never become readable."""
    if not _collector(ctx, rig).exclude(rel_path):
        raise click.ClickException(f"{rel_path} isn't a pending file of {rig}")
    click.echo(f"excluded {rel_path}")


# ── storage ──────────────────────────────────────────────────────────────
@click.group()
def storage() -> None:
    """NAS, S3 backup, cleanup and catalog backups."""


@storage.command("status")
@pass_ctx
def storage_status(ctx: Ctx) -> None:
    """Per location: reachable, replicas and bytes, missing or corrupt copies."""
    for row in ctx.catalog.query("SELECT r.location, count(*) AS n, sum(b.size_bytes) AS bytes, sum(r.state = 'present') AS present, "
                                 "sum(r.state IN ('missing', 'corrupt')) AS bad FROM replicas r JOIN blobs b USING (sha256) GROUP BY r.location"):
        loc = ctx.catalog.one("SELECT reachable, last_probe_at FROM locations WHERE name = ?", (row["location"],))
        reach = "" if loc is None else (" reachable" if loc["reachable"] else " UNREACHABLE")
        click.echo(f"{row['location']}:{reach} {row['present']} present ({human_bytes(row['bytes'])}), {row['bad']} missing/corrupt")
    at_risk = ctx.catalog.one("SELECT count(*) AS n FROM issues WHERE kind = 'DATA_AT_RISK' AND status = 'open'")["n"]
    click.echo(f"DATA_AT_RISK open: {at_risk}")


@storage.command("locate")
@click.argument("what")
@pass_ctx
def storage_locate(ctx: Ctx, what: str) -> None:
    """Every replica of a blob, by SHA-256 or logical path."""
    rows = ctx.catalog.query("SELECT * FROM blobs WHERE sha256 = ? OR logical_path = ?", (what, what))
    if not rows:
        raise click.ClickException(f"no blob {what}")
    for b in rows:
        click.echo(f"{b['sha256']} {b['data_class']} {b['logical_path']} ({human_bytes(b['size_bytes'])})")
        for r in ctx.catalog.query("SELECT * FROM replicas WHERE sha256 = ?", (b["sha256"],)):
            extra = f" {r['storage_class']}" if r["storage_class"] else ""
            click.echo(f"  {r['location']}: {r['state']}{extra} verified {r['verified_at'] or '-'} {r['uri']}")


@storage.group("backup")
def storage_backup() -> None:
    """S3 backup progress."""


@storage_backup.command("status")
@pass_ctx
def backup_status(ctx: Ctx) -> None:
    from altair.storage.replicator import Replicator

    replicator = Replicator(ctx.catalog, ctx.config, ctx.s3())
    for row in replicator.status():
        click.echo(f"{row['class']:<20} {row['blobs']:>7} blobs {human_bytes(row['bytes']):>10}  in S3 {row['in_s3']:>7}  pending {row['pending']}")
    issues = ctx.catalog.query("SELECT kind, message FROM issues WHERE kind IN ('BACKUP_BEHIND', 'DATA_AT_RISK') AND status = 'open'")
    for issue in issues:
        click.echo(f"{issue['kind']}: {issue['message']}")


@storage_backup.command("run")
@click.option("--max-items", type=int)
@pass_ctx
def backup_run(ctx: Ctx, max_items: int | None) -> None:
    """Upload what is pending now (altaird does this continuously)."""
    from altair.storage.replicator import Replicator

    report = Replicator(ctx.catalog, ctx.config, ctx.s3()).run_once(max_items)
    click.echo(f"uploaded {report.uploaded} ({human_bytes(report.bytes)}), deferred {report.deferred}, no source {report.no_source}")
    for failure in report.failed:
        click.echo(f"  {failure}", err=True)


@storage.command("cleanup")
@click.option("--dry-run", is_flag=True)
@click.option("--location", help="spool, nas, s3, rig or rig:<name>")
@pass_ctx
def storage_cleanup(ctx: Ctx, dry_run: bool, location: str | None) -> None:
    """Apply the retention rules (SPEC §7.6); prints what was freed."""
    from altair.storage.cleanup import Cleaner

    report = Cleaner(ctx.catalog, ctx.config, s3=ctx.s3(required=False)).run(dry_run=dry_run or None, location=location)
    if report.blocked:
        raise click.ClickException(f"cleanup is stopped: {report.blocked}")
    verb = "would delete" if report.dry_run else "deleted"
    for c in report.deleted:
        click.echo(f"{verb} [{c.rule}] {c.uri}")
    click.echo(f"{verb} {len(report.deleted)} file(s), {human_bytes(report.freed_bytes)}; skipped {len(report.skipped)}")
    for c, why in report.skipped[:50]:
        click.echo(f"  kept {c.uri}: {why}")


@storage.command("ledger")
@click.option("--since")
@pass_ctx
def storage_ledger(ctx: Ctx, since: str | None) -> None:
    """Every deletion and the backup it relied on."""
    from altair.storage.cleanup import ledger

    for row in ledger(ctx.catalog, since):
        relied = ", ".join(r["location"] for r in json.loads(row["relied_on_json"]))
        click.echo(f"{row['deleted_at']} {'(dry run) ' if row['dry_run'] else ''}{row['rule']} {row['uri']} relied on: {relied or '-'}")


@storage.group("nas")
def storage_nas() -> None:
    """NAS setup and health."""


@storage_nas.command("init")
@click.option("--adopt", is_flag=True, help="Use a NAS that is already initialised")
@pass_ctx
def nas_init(ctx: Ctx, adopt: bool) -> None:
    from altair.storage import nas as nas_mod

    try:
        identity = nas_mod.init(ctx.catalog, ctx.config, adopt=adopt)
    except nas_mod.NasError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"NAS ready: {ctx.config.storage.nas.root} (identity {identity['id']})")


@storage_nas.command("status")
@pass_ctx
def nas_status(ctx: Ctx) -> None:
    from altair.storage import nas as nas_mod

    health = nas_mod.check(ctx.catalog, ctx.config)
    free = f"{health.free_percent:.1f}%" if health.free_percent is not None else "unknown"
    click.echo(f"reachable {health.reachable}, healthy {health.healthy}, free {free}" + (f", {health.reason}" if health.reason else ""))
    sys.exit(0 if health.usable else 1)


@storage.group("s3")
def storage_s3() -> None:
    """S3 bucket setup and checks."""


@storage_s3.command("init")
@click.option("--profile", help="An admin AWS profile (the daemon's own credentials can't configure the bucket)")
@pass_ctx
def s3_init(ctx: Ctx, profile: str | None) -> None:
    import boto3

    from altair.storage import s3_setup

    cfg = ctx.s3().cfg
    session = boto3.session.Session(profile_name=profile) if profile else boto3.session.Session()
    client = session.client("s3", region_name=cfg.region, endpoint_url=cfg.endpoint_url)
    s3_setup.init(client, cfg)
    click.echo(f"Bucket {cfg.bucket}: versioning, encryption, public access block and lifecycle rules set.")
    click.echo("IAM policy for the daemon's credentials (no delete permission on kept data):")
    click.echo(s3_setup.policy_json(cfg))


@storage_s3.command("policy")
@pass_ctx
def s3_policy(ctx: Ctx) -> None:
    from altair.storage import s3_setup

    click.echo(s3_setup.policy_json(ctx.s3().cfg))


@storage_s3.command("check")
@pass_ctx
def s3_check(ctx: Ctx) -> None:
    from altair.storage import s3_setup

    s3 = ctx.s3()
    results = s3_setup.check(s3.client, s3.cfg)
    ok = all(check_line(label, passed, detail) for label, passed, detail in results)
    s3_setup.flag(ctx.catalog, ctx.config, results)
    sys.exit(0 if ok else 1)


@storage.command("backup-catalog")
@pass_ctx
def backup_catalog(ctx: Ctx) -> None:
    """Back the catalog up to the NAS now (altaird does it nightly)."""
    from altair.storage import catalog_backup

    sha, logical = catalog_backup.backup(ctx.catalog, ctx.config, ctx.nas())
    click.echo(f"catalog backed up to {logical} ({sha[:12]}…); the replicator uploads it to S3")


@storage.command("restore-catalog")
@click.option("--latest", is_flag=True, default=True)
@click.option("--name", help="A specific backup, e.g. altair-20260925T101500Z.db.zst")
@click.option("--from", "source", type=click.Choice(["any", "nas", "s3"]), default="any")
@pass_ctx
def restore_catalog(ctx: Ctx, latest: bool, name: str | None, source: str) -> None:
    """Replace the catalog with a backup (stop altaird first)."""
    from altair.storage import catalog_backup
    from altair.storage.locations import nas_location

    nas = nas_location(ctx.config) if source in ("any", "nas") else None
    s3 = ctx.s3(required=False) if source in ("any", "s3") else None
    try:
        path = catalog_backup.restore(ctx.config, nas=nas, s3=s3, name=name)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"catalog restored to {path}; the previous one is kept beside it")


COMMANDS = [rigs, collect, storage]
