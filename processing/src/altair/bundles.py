"""Zip bundles in S3.

**Calibrated subs** (``calibrated_bundle``): one zip per final night stack
(project, night, filter) is the S3 copy of that night's calibrated subs,
instead of one object per sub. It means:
- fewer objects, so fewer lifecycle transition fees and less per-object cold
  storage overhead;
- one restore per night instead of hundreds;
- one download link in the Hub.

The zip is *stored*, not compressed: the subs are already compressed XISF.
So every member's bytes sit at a known offset, recorded in
``bundle_members``, and Altair still reads a single sub with a ranged GET.
On the NAS the subs stay individual files for the retention period.

**Masters** (``masters_bundle``): one zip per project (a target on a rig)
holding the latest multi-night master of each filter and its report, rebuilt
after every merge. It lives under one fixed key (`<project>/bundles/masters.zip`):
- S3 versioning keeps the previous zips;
- a lifecycle rule expires them after ``masters_bundle_noncurrent_days``;
- the daemon needs no delete permission for this.

The masters themselves remain individual objects, which processing needs.
Both zips are built deterministically (fixed timestamps and member order),
so an unchanged set of files gives the same SHA-256 and nothing is uploaded
again.
"""
from __future__ import annotations

import json
import logging
import struct
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.planner.projects import project_label
from altair.storage import blobs

log = logging.getLogger("altair.bundles")
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
LOCAL_HEADER = struct.Struct("<IHHHHHIIIHH")   # the zip local file header, 30 bytes


def build_zip(dest: Path, members: Iterable[tuple[str, Path | bytes]]) -> Path:
    """A stored (uncompressed), deterministic zip; ZIP64 when needed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for name, content in sorted(members, key=lambda m: m[0]):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            if isinstance(content, bytes):
                z.writestr(info, content)
            else:
                with open(content, "rb") as src, z.open(info, "w", force_zip64=True) as out:
                    while block := src.read(4 << 20):
                        out.write(block)
    partial.replace(dest)
    return dest


def member_offsets(path: Path) -> dict[str, tuple[int, int]]:
    """name → (offset of the member's data, size), read from the local headers."""
    out = {}
    with zipfile.ZipFile(path) as z, open(path, "rb") as raw:
        for info in z.infolist():
            raw.seek(info.header_offset)
            fields = LOCAL_HEADER.unpack(raw.read(LOCAL_HEADER.size))
            name_len, extra_len = fields[9], fields[10]
            out[info.filename] = (info.header_offset + LOCAL_HEADER.size + name_len + extra_len, info.file_size)
    return out


def readable_path(catalog: Catalog, sha: str) -> Path | None:
    for location in ("cache", "nas"):
        row = blobs.replicas(catalog.conn, sha).get(location)
        if blobs.verified(row) and Path(row["uri"]).exists():
            return Path(row["uri"])
    return None


# ── calibrated subs ──────────────────────────────────────────────────────
def calibrated(publisher, *, base: str, night: str, rig: str, frames: list[dict], work_dir: Path) -> str | None:
    """Zip a final night's calibrated subs; ``frames`` are {sha256, name,
    path, source_sha256}. Registers the zip (cache now, S3 through the
    replicator) and where every sub sits in it. Returns the zip's SHA-256."""
    if not frames:
        return None
    manifest = {"schema": 1, "night": night, "rig": rig, "frames": [{k: f[k] for k in ("sha256", "name", "source_sha256")} for f in frames]}
    path = build_zip(work_dir / "calibrated.zip", [(f["name"], Path(f["path"])) for f in frames]
                     + [("manifest.json", json.dumps(manifest, indent=1, sort_keys=True).encode())])
    offsets = member_offsets(path)
    sha, logical, _ = publisher.store(path, lambda s: f"{base}/calibrated_{s[:8]}.zip", "calibrated_bundle", rig=rig, to_nas=False)
    with publisher.catalog.transaction() as tx:
        for f in frames:
            offset, size = offsets[f["name"]]
            tx.execute("INSERT OR IGNORE INTO bundle_members(bundle_sha256, member_sha256, name, data_offset, size) VALUES (?, ?, ?, ?, ?)",
                       (sha, f["sha256"], f["name"], offset, size))
    path.unlink(missing_ok=True)
    return sha


def mark_members_backed_up(tx, s3, bundle_sha: str, logical_path: str) -> int:
    """After the zip's upload: each member has a verified S3 copy (inside it)."""
    rows = tx.execute("SELECT member_sha256, name FROM bundle_members WHERE bundle_sha256 = ?", (bundle_sha,)).fetchall()
    for row in rows:
        if tx.execute("SELECT 1 FROM blobs WHERE sha256 = ?", (row["member_sha256"],)).fetchone():
            blobs.set_replica(tx, row["member_sha256"], "s3_bundle", f"{s3.uri(logical_path)}#{row['name']}", kind="s3", method="s3_bundle")
    return len(rows)


def member(catalog: Catalog, sha: str):
    """The bundle holding ``sha`` whose S3 copy is present: (member row, bundle blob, bundle's S3 replica)."""
    for row in catalog.query("SELECT * FROM bundle_members WHERE member_sha256 = ?", (sha,)):
        s3 = blobs.replicas(catalog.conn, row["bundle_sha256"]).get("s3")
        if s3 and s3["state"] in ("present", "archived_cold", "restoring", "restored"):
            return row, blobs.blob(catalog.conn, row["bundle_sha256"]), s3
    return None


# ── masters per target ───────────────────────────────────────────────────
def masters(catalog: Catalog, config: AltairConfig, project_id: int) -> str | None:
    """(Re)build the project's masters zip: the latest multi-night master of
    each filter, with its report. Returns its SHA-256 (None if nothing to zip)."""
    from altair.publish.publisher import Publisher
    from altair.reports import report_path

    project = catalog.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    latest = catalog.query("SELECT * FROM multi_night_masters m WHERE project_id = ? AND version = (SELECT max(version) FROM multi_night_masters "
                           "WHERE project_id = m.project_id AND filter = m.filter) ORDER BY filter", (project_id,))
    label = project_label(project)
    members: list[tuple[str, Path | bytes]] = []
    contents = []
    for m in latest:
        path = readable_path(catalog, m["sha256"])
        if path is None:
            log.warning("masters zip for %s: %s v%s isn't readable locally; left out", label, m["filter"], m["version"])
            continue
        name = f"{label}_{m['filter']}_v{m['version']:03d}{path.suffix}"
        members.append((name, path))
        report = report_path(config, "multi_night_master", m["id"])
        if report.exists():
            members.append((f"{label}_{m['filter']}_v{m['version']:03d}_report.md", report))
        contents.append({"filter": m["filter"], "version": m["version"], "name": name, "sha256": m["sha256"], "nights": m["n_nights"],
                         "total_exposure_s": m["total_exposure_s"]})
    if not members:
        return None
    readme = "\n".join([f"{label}: latest multi-night masters", ""] + [
        f"- {c['name']}: {c['filter']} v{c['version']}, {c['nights']} night(s), {(c['total_exposure_s'] or 0) / 3600:.1f} h" for c in contents]) + "\n"
    members.append(("README.txt", readme.encode()))
    config.paths.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="masters-zip-", dir=config.paths.work_dir) as tmp:
        path = build_zip(Path(tmp) / f"masters_{project_id}.zip", members)
        sha, _, _ = Publisher(catalog, config).store(path, lambda s: f"{project['path']}/bundles/masters.zip", "masters_bundle",
                                                     rig=project["rig"], to_nas=False)
    with catalog.transaction() as tx:
        known = tx.execute("SELECT sha256, build FROM masters_bundles WHERE project_id = ?", (project_id,)).fetchone()
        if known is None or known["sha256"] != sha:
            tx.execute("INSERT INTO masters_bundles(project_id, sha256, built_at, contents_json, build) VALUES (?, ?, ?, ?, 1) "
                       "ON CONFLICT(project_id) DO UPDATE SET sha256 = excluded.sha256, built_at = excluded.built_at, "
                       "contents_json = excluded.contents_json, build = masters_bundles.build + 1",
                       (project_id, sha, now_iso(), json.dumps(contents)))
        from altair.publish import products

        products.enqueue(tx, config, "masters_bundle", project_id)
    return sha


def supersede_masters_zip(tx, sha: str, logical_path: str) -> None:
    """Older zips under the same key are now noncurrent S3 versions (expired by lifecycle)."""
    tx.execute("UPDATE replicas SET state = 'missing', missing_reason = 'superseded' WHERE location = 's3' AND sha256 IN "
               "(SELECT sha256 FROM blobs WHERE logical_path = ? AND data_class = 'masters_bundle' AND sha256 != ?)", (logical_path, sha))
