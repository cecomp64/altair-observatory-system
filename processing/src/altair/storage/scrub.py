"""Integrity scrubbing (SPEC §7.8). Every ``scrub_interval_days`` a random
``scrub_sample_percent`` of NAS and S3 replicas is re-verified: the NAS by a
full re-hash; S3 by a checksum HeadObject, plus a full download for 10% of
the sample (cold classes are skipped, so nothing is restored).

A missing NAS file (not removed by cleanup) raises ``NAS_FILE_MISSING`` and
is healed; a corrupt replica raises ``INTEGRITY_MISMATCH`` and is rewritten
from a good copy. These are the only places a committed file's content is
written again, and always with the catalog's SHA-256. Nothing is scrubbed,
marked missing or healed while the NAS is unhealthy (§7.10).
"""
from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.hashing import sha256_file
from altair.issues import raise_issue
from altair.storage import blobs
from altair.storage import nas as nas_mod
from altair.storage.locations import COLD_CLASSES, IntegrityMismatch, S3Location
from altair.storage.stager import Stager

STATE_KEY = "scrub_last_run"


@dataclass
class ScrubReport:
    checked: int = 0
    missing: list[str] = field(default_factory=list)
    corrupt: list[str] = field(default_factory=list)
    healed: list[str] = field(default_factory=list)
    skipped: str | None = None


class Scrubber:
    def __init__(self, catalog: Catalog, config: AltairConfig, *, s3: S3Location | None = None,
                 clock: Callable[[], datetime] | None = None, rng: random.Random | None = None):
        self.catalog = catalog
        self.config = config
        self.s3 = s3
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rng = rng or random.Random()
        self.stager = Stager(catalog, config, s3=s3, clock=self.clock)

    def due(self) -> bool:
        last = self.catalog.get_state(STATE_KEY)
        return last is None or self.clock() - datetime.fromisoformat(last) >= timedelta(days=self.config.storage.verify.scrub_interval_days)

    def run(self, *, location: str | None = None, sample_percent: float | None = None) -> ScrubReport:
        report = ScrubReport()
        percent = self.config.storage.verify.scrub_sample_percent if sample_percent is None else sample_percent
        if location in (None, "nas"):
            health = nas_mod.check(self.catalog, self.config)
            if not health.usable:
                report.skipped = f"the NAS isn't usable ({health.reason})"
            else:
                self._nas(report, percent)
        if location in (None, "s3") and self.s3 is not None:
            self._s3(report, percent)
        self.catalog.set_state(STATE_KEY, self.clock().isoformat())
        return report

    def _sample(self, rows: list, percent: float) -> list:
        if not rows:
            return []
        n = max(1, round(len(rows) * percent / 100))
        return self.rng.sample(rows, min(n, len(rows)))

    def _nas(self, report: ScrubReport, percent: float) -> None:
        rows = self.catalog.query("SELECT r.*, b.logical_path, b.data_class, b.size_bytes FROM replicas r JOIN blobs b USING (sha256) "
                                  "WHERE r.location = 'nas' AND r.state = 'present'")
        for r in self._sample(rows, percent):
            report.checked += 1
            path = Path(r["uri"])
            if not path.exists():
                report.missing.append(r["logical_path"])
                with self.catalog.transaction() as tx:
                    blobs.mark_missing(tx, r["sha256"], "nas", "external_delete")
                    raise_issue(tx, self.config, kind="NAS_FILE_MISSING", severity="warning", fingerprint=f"NAS_FILE_MISSING:{r['sha256']}",
                                message=f"{r['logical_path']} disappeared from the NAS without a cleanup entry; it is restored from S3 or the rig.",
                                scope={"sha256s": [r["sha256"]]})
            elif sha256_file(path) != r["sha256"]:
                report.corrupt.append(r["logical_path"])
                with self.catalog.transaction() as tx:
                    blobs.mark_corrupt(tx, r["sha256"], "nas")
                    raise_issue(tx, self.config, kind="INTEGRITY_MISMATCH", severity="warning", fingerprint=f"INTEGRITY_MISMATCH:nas:{r['sha256']}",
                                message=f"The NAS copy of {r['logical_path']} failed its SHA-256; it is rewritten from a good copy.",
                                scope={"sha256s": [r["sha256"]]})
            else:
                self.catalog.execute("UPDATE replicas SET verified_at = datetime('now') WHERE sha256 = ? AND location = 'nas'", (r["sha256"],))
                continue
            if self._heal_nas(r):
                report.healed.append(r["logical_path"])

    def _heal_nas(self, r) -> bool:
        info = blobs.blob(self.catalog.conn, r["sha256"])
        source = self.stager._source(r["sha256"], info, nas_ok=False, allow_s3_fallback=True)
        if source is None or source[0] == "s3_cold":
            return False   # healed later, when a copy is readable without a restore
        try:
            self.stager._fetch(r["sha256"], info, source)
        except (IntegrityMismatch, OSError):
            return False
        return blobs.verified(blobs.replicas(self.catalog.conn, r["sha256"]).get("nas"))

    def _s3(self, report: ScrubReport, percent: float) -> None:
        rows = self.catalog.query("SELECT r.*, b.logical_path, b.data_class, b.size_bytes FROM replicas r JOIN blobs b USING (sha256) "
                                  "WHERE r.location = 's3' AND r.state IN ('present', 'restored')")
        rows = [r for r in rows if (r["storage_class"] or "") not in COLD_CLASSES]
        sample = self._sample(rows, percent)
        full = set(self.rng.sample(range(len(sample)), max(1, len(sample) // 10))) if sample else set()
        for i, r in enumerate(sample):
            report.checked += 1
            ok = self.s3.verify_fresh(r["logical_path"], r["sha256"], r["size_bytes"]) is not None
            if ok and i in full:
                with tempfile.TemporaryDirectory() as tmp:
                    try:
                        self.s3.download(r["logical_path"], Path(tmp) / "blob", r["sha256"])
                    except IntegrityMismatch:
                        ok = False
            if ok:
                continue
            report.corrupt.append(r["logical_path"])
            with self.catalog.transaction() as tx:
                blobs.mark_corrupt(tx, r["sha256"], "s3")
                raise_issue(tx, self.config, kind="INTEGRITY_MISMATCH", severity="warning", fingerprint=f"INTEGRITY_MISMATCH:s3:{r['sha256']}",
                            message=f"The S3 copy of {r['logical_path']} failed verification; it is re-uploaded from a good copy.",
                            scope={"sha256s": [r["sha256"]]})
            good = self._good_local(r["sha256"])
            if good:
                info = self.s3.rewrite(good, r["logical_path"], r["sha256"], r["data_class"])
                with self.catalog.transaction() as tx:
                    blobs.set_replica(tx, r["sha256"], "s3", self.s3.uri(r["logical_path"]), kind="s3", method="s3_checksum_sha256",
                                      storage_class=info.storage_class, version_id=info.version_id)
                report.healed.append(r["logical_path"])

    def _good_local(self, sha: str) -> Path | None:
        for location, row in blobs.replicas(self.catalog.conn, sha).items():
            if location != "s3" and blobs.verified(row) and Path(row["uri"]).exists() and sha256_file(row["uri"]) == sha:
                return Path(row["uri"])
        return None
