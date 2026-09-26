"""The collection manifest (SPEC §7.3): one ``metadata`` blob per rig-night,
written when the night closes, listing every collected file with its hash
and headers, so the catalog can be rebuilt without reading any image
(SPEC §7.9). A late frame writes a new version; old ones are kept."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from altair import __version__
from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.hashing import sha256_file
from altair.storage import blobs
from altair.storage.locations import FsLocation

SCHEMA = 1


def manifest_path(rig: str, night: str, version: int) -> str:
    return f"raw/{rig}/_manifests/{night}.json" if version == 1 else f"raw/{rig}/_manifests/{night}.v{version}.json"


def build(catalog: Catalog, config: AltairConfig, rig: str, night: str, closed_by: str) -> dict:
    rows = catalog.query(
        "SELECT f.sha256, f.raw_headers_json, b.logical_path, b.size_bytes, b.data_class, rf.rel_path "
        "FROM frames f JOIN blobs b ON b.sha256 = f.sha256 LEFT JOIN rig_files rf ON rf.rig = f.rig AND rf.sha256 = f.sha256 "
        "WHERE f.rig = ? AND f.night = ? AND f.origin = 'collect' ORDER BY b.logical_path", (rig, night))
    return {
        "schema": SCHEMA, "rig": rig, "raw_root": config.rigs[rig].raw_root, "night": night, "closed_by": closed_by,
        "collector_version": __version__,
        "files": [{"logical_path": r["logical_path"], "rig_path": r["rel_path"], "sha256": r["sha256"], "size": r["size_bytes"],
                   "class": r["data_class"], "headers": json.loads(r["raw_headers_json"] or "{}")} for r in rows],
    }


def write(catalog: Catalog, config: AltairConfig, nas: FsLocation, rig: str, night: str, closed_by: str) -> str:
    """Write the manifest to the NAS as a verified metadata blob (queued for
    S3 by the replicator). Returns its SHA-256."""
    manifest = build(catalog, config, rig, night, closed_by)
    existing = catalog.query("SELECT logical_path FROM blobs WHERE data_class = 'metadata' AND logical_path LIKE ?",
                             (f"raw/{rig}/_manifests/{night}%.json",))
    version = len(existing) + 1
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=config.paths.spool_dir if _mk(config.paths.spool_dir) else None,
                                     encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True, default=str)
        temp = Path(handle.name)
    try:
        sha = sha256_file(temp)
        known = catalog.one("SELECT logical_path FROM blobs WHERE sha256 = ?", (sha,))
        if known:
            return sha   # identical to the last manifest (nothing changed)
        logical = manifest_path(rig, night, version)
        uri = nas.write_verified(temp, logical, sha)
        with catalog.transaction() as tx:
            blobs.add_blob(tx, sha, temp.stat().st_size, "metadata", logical, rig)
            blobs.set_replica(tx, sha, "nas", uri)
        return sha
    finally:
        temp.unlink(missing_ok=True)


def _mk(path: Path) -> bool:
    path.mkdir(parents=True, exist_ok=True)
    return True
