"""Catalog backups (SPEC §7.9). The catalog lives on the processing PC's
local disk (SQLite over SMB is unsafe). Nightly: SQLite online backup →
zstd → ``catalog/altair-<utc>.db.zst`` on the NAS as a metadata blob, which
the replicator uploads to S3. Cleanup keeps 30 daily and 12 monthly."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import zstandard

from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.hashing import sha256_file
from altair.storage import blobs
from altair.storage.locations import FsLocation, S3Location

PREFIX = "catalog/"


def backup(catalog: Catalog, config: AltairConfig, nas: FsLocation, *, now: datetime | None = None) -> tuple[str, str]:
    """Back the catalog up to the NAS. Returns (sha256, logical path)."""
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work = Path(tempfile.mkdtemp(prefix="altair-catalog-"))
    try:
        snapshot = work / "catalog.db"
        target = sqlite3.connect(snapshot)
        with catalog._lock:
            catalog.conn.backup(target)
        target.close()
        packed = work / f"altair-{stamp}.db.zst"
        with snapshot.open("rb") as src, packed.open("wb") as dst:
            zstandard.ZstdCompressor(level=10).copy_stream(src, dst)
        sha = sha256_file(packed)
        logical = f"{PREFIX}{packed.name}"
        uri = nas.write_verified(packed, logical, sha)
        with catalog.transaction() as tx:
            blobs.add_blob(tx, sha, packed.stat().st_size, "metadata", logical)
            blobs.set_replica(tx, sha, "nas", uri)
        return sha, logical
    finally:
        for path in work.iterdir():
            path.unlink(missing_ok=True)
        work.rmdir()


def list_backups(nas: FsLocation | None = None, s3: S3Location | None = None) -> list[tuple[str, str]]:
    """Available backups as (name, source), newest first."""
    found: dict[str, str] = {}
    if s3 is not None:
        for obj in s3.list(PREFIX):
            found.setdefault(obj["Key"].rsplit("/", 1)[-1], "s3")
    if nas is not None and (nas.root / "catalog").is_dir():
        for path in (nas.root / "catalog").glob("altair-*.db.zst"):
            found[path.name] = "nas"
    return sorted(found.items(), reverse=True)


def restore(config: AltairConfig, *, nas: FsLocation | None = None, s3: S3Location | None = None, name: str | None = None) -> Path:
    """Restore a backup (the newest, or ``name``) as the catalog. The current
    catalog file, if any, is kept beside it as ``.replaced-<utc>``. Run with
    altaird stopped."""
    backups = list_backups(nas, s3)
    if name:
        backups = [b for b in backups if b[0] == name]
    if not backups:
        raise FileNotFoundError("no catalog backup found on the NAS or in S3")
    chosen, source = backups[0]
    target = config.catalog_path
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="altair-restore-"))
    try:
        packed = work / chosen
        if source == "nas":
            packed.write_bytes((nas.root / "catalog" / chosen).read_bytes())
        else:
            s3.client.download_file(s3.bucket, s3.key(f"{PREFIX}{chosen}"), str(packed))
        restored = work / "catalog.db"
        with packed.open("rb") as src, restored.open("wb") as dst:
            zstandard.ZstdDecompressor().copy_stream(src, dst)
        sqlite3.connect(restored).execute("PRAGMA integrity_check").fetchone()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for suffix in ("", "-wal", "-shm"):
            current = Path(str(target) + suffix)
            if current.exists():
                os.replace(current, Path(f"{target}{suffix}.replaced-{stamp}"))
        os.replace(restored, target)
        return target
    finally:
        for path in work.iterdir():
            path.unlink(missing_ok=True)
        work.rmdir()
