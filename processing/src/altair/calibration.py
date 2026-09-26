"""The calibration library (SPEC §8): masters Altair built, plus masters
imported from elsewhere (`altair calib import`), e.g. a flat library made
before Altair existed. An imported master is matched like any other and can
resolve open issues."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.hub.reporters import enqueue_calibration_master
from altair.ingest.headers import normalize, read_header
from altair.publish.publisher import Publisher

KINDS = ("BIAS", "DARK", "DARKFLAT", "FLAT")


class ImportError_(Exception):
    pass


def import_master(catalog: Catalog, config: AltairConfig, path: str | Path, *, kind: str, rig: str,
                  overrides: dict[str, Any] | None = None) -> dict:
    """Register ``path`` as a master: header fields, overridden by
    ``overrides``; stored like a built master (NAS, cache, S3 later)."""
    kind = kind.upper()
    if kind not in KINDS:
        raise ImportError_(f"kind must be one of {', '.join(KINDS)}")
    if rig not in config.rigs:
        raise ImportError_(f"unknown rig {rig!r}")
    rig_cfg = config.rigs[rig]
    fields = normalize(read_header(path), config, rig_cfg, Path(path).name)
    fields.update({k: v for k, v in (overrides or {}).items() if v is not None})
    night = fields.get("night")
    if not night:
        raise ImportError_("no DATE-OBS in the header: pass --night")
    night = str(night)
    if kind == "FLAT" and not fields.get("filter"):
        raise ImportError_("a flat needs a filter: pass --filter")
    publisher = Publisher(catalog, config)
    suffix = Path(path).suffix.lower()
    sha, logical, _ = publisher.store(path, lambda s: f"calibration/masters/{kind.lower()}/{rig}/{night}/imported_{s[:8]}{suffix}",
                                      "calibration_master", rig=rig)
    with catalog.transaction() as tx:
        tx.execute(
            "INSERT OR IGNORE INTO calibration_masters(kind, sha256, source_frames_json, camera, telescope, filter, focal_length, exposure, gain, "
            "offset, sensor_temp, binning, readout_mode, width, height, rotator_pos, rotator_units, rig, night, n_frames, taken_at, imported) "
            "VALUES (?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (kind, sha, fields.get("camera") or rig_cfg.camera, fields.get("telescope") or rig_cfg.telescope,
             fields.get("filter") if kind == "FLAT" else None, fields.get("focal_length") or rig_cfg.focal_length_mm, fields.get("exposure"),
             _int(fields.get("gain")), _int(fields.get("offset")), fields.get("sensor_temp"), fields.get("binning") or "1x1",
             fields.get("readout_mode"), _int(fields.get("width")), _int(fields.get("height")),
             fields.get("rotator_pos") if kind == "FLAT" else None, rig_cfg.rotator.units if kind == "FLAT" else None, rig, night,
             _int(fields.get("n_frames")) or 1, fields["date_obs"].isoformat() if hasattr(fields.get("date_obs"), "isoformat") else f"{night}T12:00:00Z"))
        master = dict(tx.execute("SELECT * FROM calibration_masters WHERE sha256 = ?", (sha,)).fetchone())
        if config.hub.enabled:
            enqueue_calibration_master(tx, config, master["id"])
        rerun = publisher.auto_resolve(tx, master)
    return {**master, "logical_path": logical, "reruns_queued": rerun}


def library(catalog: Catalog, *, kind: str | None = None, rig: str | None = None, include_superseded: bool = False) -> list[dict]:
    clauses, args = ["1 = 1"], []
    if kind:
        clauses.append("kind = ?")
        args.append(kind.upper())
    if rig:
        clauses.append("rig = ?")
        args.append(rig)
    if not include_superseded:
        clauses.append("superseded_by IS NULL")
    rows = catalog.query(f"SELECT * FROM calibration_masters WHERE {' AND '.join(clauses)} ORDER BY rig, kind, night", tuple(args))
    return [{**dict(r), "sources": len(json.loads(r["source_frames_json"] or "[]"))} for r in rows]


def _int(value: Any) -> int | None:
    return None if value is None else int(round(float(value)))
