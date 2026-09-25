"""`altair index <dir> --rig R` (SPEC §17.7): catalogue existing FITS/XISF
files from one of the observatory's rigs, in place and read-only; hash,
read headers, resolve Hub targets, report frames with origin "import".
Replaces the retired astrophotography-database desktop indexer."""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from altair import frames as frame_ops
from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.hub import resolver
from altair.hub.config_sync import HubConfig
from altair.ingest.headers import FITS_SUFFIXES, XISF_SUFFIXES, normalize, read_header
from altair.issues import raise_issue

SUFFIXES = FITS_SUFFIXES | XISF_SUFFIXES


@dataclass
class IndexReport:
    seen: int = 0
    indexed: int = 0
    already_known: int = 0
    linked: int = 0
    unlinked: int = 0
    skipped_unknown_rig: int = 0
    errors: list[str] = field(default_factory=list)


def sha256_file(path: Path, chunk: int = 4 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def _rig_matches(fields: dict, rig) -> bool:
    return fields["telescope"] == rig.telescope and fields["camera"] == rig.camera


def index(catalog: Catalog, config: AltairConfig, root: str | Path, *, rig: str, hub_config: HubConfig | None,
          nas_root: str | Path | None = None, adopt: bool = False, dry_run: bool = False) -> IndexReport:
    rig_cfg = config.rigs.get(rig)
    if rig_cfg is None:
        raise ValueError(f"unknown rig {rig!r}; configured rigs: {', '.join(config.rigs)}")
    if adopt and not nas_root:
        raise ValueError("--adopt needs the NAS root (storage.locations nas.root in altair.yaml)")
    root = Path(root)
    nas = Path(nas_root).resolve() if nas_root else None
    report = IndexReport()

    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES):
        report.seen += 1
        try:
            header = read_header(path)
            fields = normalize(header, config, rig_cfg, path.name)
            if not fields["image_type"] or not fields["date_obs"]:
                raise ValueError("missing IMAGETYP or DATE-OBS")
            if not _rig_matches(fields, rig_cfg):
                report.skipped_unknown_rig += 1
                if not dry_run:
                    with catalog.transaction() as tx:
                        raise_issue(tx, config, kind="UNKNOWN_RIG", severity="warning", fingerprint=f"UNKNOWN_RIG:index:{path.parent}",
                                    message=f"{path.parent}: headers ({fields['telescope']}, {fields['camera']}) don't match rig {rig}; skipped.",
                                    scope={"rig": rig, "path": str(path.parent)})
                continue

            resolution = None
            if fields["image_type"] == "light" and hub_config and rig_cfg.hub:
                telescope, train = rig_cfg.hub.telescope, rig_cfg.hub.optical_train
                fields["filter"] = hub_config.canonical_filter(telescope, train, fields["raw_filter"]) or fields["filter"]
                r = resolver.resolve(object_header=fields["target"], ra=fields["ra_deg"], dec=fields["dec_deg"], telescope=telescope,
                                     optical_train=train, config=hub_config, rules=config.hub.resolve)
                resolution = (r.target_id, r.source)
            if dry_run:
                report.indexed += 1
                report.linked += int(bool(resolution and resolution[0]))
                continue

            sha = sha256_file(path)
            location, uri = _placement(path, nas, rig, fields, sha, adopt)
            night = str(fields["night"])
            rel = path.relative_to(root).as_posix()
            logical = f"raw/{rig}/{rel}" if location != "nas" or not adopt else f"raw/{rig}/{night}/{path.name}"
            frame_id, created = frame_ops.register(
                catalog, config, sha256=sha, size=path.stat().st_size, logical_path=logical,
                data_class="raw_light" if fields["image_type"] == "light" else "raw_calibration",
                location=location, uri=uri, fields=fields, rig=rig, origin="import", file_name=path.name,
                headers=header, resolution=resolution,
            )
            if created:
                report.indexed += 1
                if resolution and resolution[0]:
                    report.linked += 1
                elif fields["image_type"] == "light":
                    report.unlinked += 1
            else:
                report.already_known += 1
        except Exception as exc:  # noqa: BLE001 - one bad file never stops an index run
            report.errors.append(f"{path}: {exc}")
    return report


def _placement(path: Path, nas: Path | None, rig: str, fields: dict, sha: str, adopt: bool) -> tuple[str, str]:
    """Files under the NAS root are `nas` replicas; others are read-only
    `external:<dir>` replicas unless --adopt copies them into the NAS layout."""
    resolved = path.resolve()
    if nas and nas in resolved.parents:
        return "nas", str(resolved)
    if adopt and nas:
        destination = nas / "raw" / rig / str(fields["night"]) / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(path, destination)
        if sha256_file(destination) != sha:
            raise OSError(f"copy of {path} to {destination} failed verification")
        return "nas", str(destination)
    return f"external:{path.parent.name or 'root'}", str(resolved)
