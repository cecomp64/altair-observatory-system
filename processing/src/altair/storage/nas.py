"""NAS setup and health (SPEC §7.10).

`altair storage nas init` creates the folder tree and writes a location
identity file. Every collector poll first checks the NAS: unreachable, full,
or *unhealthy* (identity file missing or different, or too many known files
gone at once, which looks like a mount problem rather than data loss). While
it isn't usable, collection pauses (frames wait on the rigs), and nothing is
healed, re-downloaded or cleaned up anywhere.
"""
from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from pathlib import Path

from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.issues import raise_issue, resolve_issue
from altair.storage.locations import FsLocation, nas_location

TREE = ["raw", "calibration/masters", "projects", "catalog", "Masters"]
STATE_KEY = "nas_identity"
HEALTH_KEY = "nas_health"
MIN_MISSING = 3   # a couple of stray deletions are NAS_FILE_MISSING (healed), not a mount problem


class NasError(Exception):
    pass


def identity_path(config: AltairConfig, nas: FsLocation) -> Path:
    return nas.root / config.storage.nas.health_check.identity_file


def init(catalog: Catalog, config: AltairConfig, *, adopt: bool = False) -> dict:
    nas = nas_location(config)
    if nas is None:
        raise NasError("storage.locations has no 'nas' entry with a root")
    if not nas.reachable():
        raise NasError(f"{nas.root} is not reachable")
    ident_file = identity_path(config, nas)
    known = catalog.get_state(STATE_KEY)
    if ident_file.exists():
        identity = json.loads(ident_file.read_text(encoding="utf-8"))
        if known and known["id"] != identity["id"] and not adopt:
            raise NasError(f"{nas.root} belongs to another Altair catalog ({identity['id']}); use --adopt to take it over")
        if not known and not adopt:
            raise NasError(f"{nas.root} is already initialised ({identity['id']}); use --adopt to use it with this catalog")
    else:
        identity = {"id": str(uuid.uuid4()), "created_at": now_iso(), "location": "nas"}
        ident_file.write_text(json.dumps(identity, indent=1), encoding="utf-8")
    for folder in TREE:
        (nas.root / folder).mkdir(parents=True, exist_ok=True)
    probe = nas.root / ".altair-write-probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    catalog.set_state(STATE_KEY, identity)
    with catalog.transaction() as tx:
        tx.execute("INSERT OR IGNORE INTO locations(name, kind, durable) VALUES ('nas', 'fs', 1)")
    return identity


@dataclass
class Health:
    reachable: bool
    healthy: bool
    free_percent: float | None
    reason: str | None = None

    @property
    def usable(self) -> bool:
        """Frames may be written and data healed or cleaned up."""
        return self.reachable and self.healthy and self.reason != "space_low"


def check(catalog: Catalog, config: AltairConfig, nas: FsLocation | None = None, *, sample_seed: int | None = None) -> Health:
    """Probe the NAS, raise or resolve NAS_UNREACHABLE / NAS_UNHEALTHY /
    NAS_SPACE_LOW, and remember the result for the other workers."""
    nas = nas or nas_location(config)
    if nas is None:
        return Health(False, False, None, "not_configured")
    cfg = config.storage.nas
    health = _probe(catalog, config, nas, sample_seed)
    with catalog.transaction() as tx:
        tx.execute("INSERT OR IGNORE INTO locations(name, kind, durable) VALUES ('nas', 'fs', 1)")
        tx.execute("UPDATE locations SET reachable = ?, last_probe_at = ? WHERE name = 'nas'", (int(health.reachable), now_iso()))
        _issue(tx, config, "NAS_UNREACHABLE", not health.reachable,
               f"The NAS ({nas.root}) is unreachable. Collection is paused (frames wait on the rigs), and so are processing and rig cleanup.")
        _issue(tx, config, "NAS_UNHEALTHY", health.reachable and not health.healthy,
               f"The NAS ({nas.root}) looks wrong: {health.reason}. Nothing is marked missing, healed or cleaned up, and collection "
               "is paused, until it is fixed (check the share is the right volume).")
        _issue(tx, config, "NAS_SPACE_LOW", health.reason == "space_low",
               f"The NAS has {health.free_percent:.1f}% free, below {cfg.min_free_percent}%. Collection is paused; frames wait on the rigs."
               if health.free_percent is not None else "")
    catalog.set_state(HEALTH_KEY, {"reachable": health.reachable, "healthy": health.healthy, "free_percent": health.free_percent,
                                   "reason": health.reason, "at": now_iso()})
    return health


def last(catalog: Catalog) -> Health | None:
    state = catalog.get_state(HEALTH_KEY)
    return Health(state["reachable"], state["healthy"], state["free_percent"], state["reason"]) if state else None


def _probe(catalog: Catalog, config: AltairConfig, nas: FsLocation, sample_seed: int | None) -> Health:
    if not nas.reachable():
        return Health(False, False, None, "unreachable")
    cfg = config.storage.nas
    known = catalog.get_state(STATE_KEY)
    ident_file = identity_path(config, nas)
    if known:
        try:
            identity = json.loads(ident_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Health(True, False, nas.free_percent(), f"identity file {ident_file.name} is missing or unreadable")
        if identity.get("id") != known["id"]:
            return Health(True, False, nas.free_percent(), f"identity {identity.get('id')} is not this catalog's NAS ({known['id']})")
    rows = catalog.query("SELECT uri FROM replicas WHERE location = 'nas' AND state = 'present'")
    if rows:
        sample = random.Random(sample_seed).sample(rows, min(len(rows), cfg.health_check.sample_size))
        missing = sum(1 for r in sample if not Path(r["uri"]).exists())
        if missing >= min(MIN_MISSING, len(sample)) and 100.0 * missing / len(sample) > cfg.health_check.max_missing_sample_percent:
            return Health(True, False, nas.free_percent(), f"{missing} of {len(sample)} sampled files are missing")
    free = nas.free_percent()
    if free is not None and free < cfg.min_free_percent:
        return Health(True, True, free, "space_low")
    return Health(True, True, free, None)


def _issue(tx, config: AltairConfig, kind: str, active: bool, message: str) -> None:
    if active:
        raise_issue(tx, config, kind=kind, severity="blocking", fingerprint=kind, message=message, scope={"location": "nas"})
    else:
        resolve_issue(tx, config, kind, resolution="auto:ok")
