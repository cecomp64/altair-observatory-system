"""Prometheus-format metrics (SPEC §13), built from the catalog on each
scrape: queue, issues, backup backlog, NAS and cache, the Hub outbox, and the
daemon's workers. Served at /metrics and optionally written to a file for
node_exporter's textfile collector (``http.metrics_file``)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable

from altair import __version__
from altair.catalog.db import Catalog
from altair.config import AltairConfig


def _labels(labels: dict[str, Any]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34)).replace(chr(10), " ")}"'
                    for k, v in sorted(labels.items()))
    return "{" + body + "}"


class Exposition:
    def __init__(self):
        self.lines: list[str] = []

    def metric(self, name: str, kind: str, help_: str, samples: Iterable[tuple[dict[str, Any], float | int | None]]) -> None:
        self.lines += [f"# HELP {name} {help_}", f"# TYPE {name} {kind}"]
        for labels, value in samples:
            if value is not None:
                self.lines.append(f"{name}{_labels(labels)} {float(value):g}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def collect(catalog: Catalog, config: AltairConfig, workers: Callable[[], list] | None = None) -> str:
    out = Exposition()
    out.metric("altair_info", "gauge", "Altair version.", [({"version": __version__}, 1)])
    statuses = ("queued", "staging", "waiting_data", "running", "succeeded", "failed", "blocked", "skipped", "superseded")
    counts = {r["status"]: r["n"] for r in catalog.query("SELECT status, count(*) AS n FROM jobs GROUP BY status")}
    out.metric("altair_jobs", "gauge", "Jobs by status.", [({"status": s}, counts.get(s, 0)) for s in statuses])
    out.metric("altair_issues_open", "gauge", "Open issues by kind and severity.",
               [({"kind": r["kind"], "severity": r["severity"]}, r["n"]) for r in catalog.query(
                   "SELECT kind, severity, count(*) AS n FROM issues WHERE status = 'open' GROUP BY kind, severity")])
    backlog = catalog.query(
        "SELECT b.data_class, count(*) AS n, coalesce(sum(b.size_bytes), 0) AS bytes FROM blobs b WHERE NOT EXISTS (SELECT 1 FROM replicas r "
        "WHERE r.sha256 = b.sha256 AND r.location = 's3' AND r.state IN ('present', 'archived_cold', 'restoring', 'restored')) GROUP BY b.data_class")
    classes = set(config.storage.s3.backup_classes) if config.storage.s3 else set()
    out.metric("altair_backup_pending_blobs", "gauge", "Blobs of backed-up classes without a verified S3 copy.",
               [({"data_class": r["data_class"]}, r["n"]) for r in backlog if r["data_class"] in classes])
    out.metric("altair_backup_pending_bytes", "gauge", "Bytes of backed-up classes without a verified S3 copy.",
               [({"data_class": r["data_class"]}, r["bytes"]) for r in backlog if r["data_class"] in classes])
    raw = next((r["n"] for r in backlog if r["data_class"] == "raw_light"), 0)
    out.metric("altair_raw_lights_without_s3", "gauge", "Collected raw lights with no verified S3 copy yet.", [({}, raw)])
    health = catalog.get_state("nas_health") or {}
    out.metric("altair_nas_usable", "gauge", "1 when the NAS is reachable and healthy.",
               [({}, int(bool(health.get("reachable") and health.get("healthy"))) if health else None)])
    out.metric("altair_nas_free_percent", "gauge", "Free space on the NAS.", [({}, health.get("free_percent"))])
    cache = catalog.one("SELECT coalesce(sum(b.size_bytes), 0) AS n FROM replicas r JOIN blobs b USING (sha256) "
                        "WHERE r.location = 'cache' AND r.state = 'present'")["n"]
    out.metric("altair_cache_bytes", "gauge", "Bytes in the local cache.", [({}, cache)])
    outbox = catalog.one("SELECT sum(sent_at IS NULL AND parked = 0) AS pending, sum(parked) AS parked FROM hub_outbox")
    out.metric("altair_hub_outbox_pending", "gauge", "Hub outbox items waiting to be sent.", [({}, outbox["pending"] or 0)])
    out.metric("altair_hub_outbox_parked", "gauge", "Hub outbox items the Hub rejected.", [({}, outbox["parked"] or 0)])
    durations = {}
    for row in catalog.query("SELECT kind, result_json FROM jobs WHERE status = 'succeeded' AND result_json IS NOT NULL ORDER BY finished_at"):
        run_s = (json.loads(row["result_json"]).get("timings") or {}).get("run_s")
        if run_s is not None:
            durations[row["kind"]] = run_s
    out.metric("altair_job_last_duration_seconds", "gauge", "PixInsight run time of the last successful job of each kind.",
               [({"kind": k}, v) for k, v in sorted(durations.items())])
    probes = catalog.query("SELECT name, reachable FROM locations WHERE name LIKE 'rig:%' OR name IN ('nas', 's3')")
    out.metric("altair_location_reachable", "gauge", "1 when a storage location or rig share answered its last probe.",
               [({"location": r["name"]}, r["reachable"]) for r in probes])
    if workers:
        rows = workers()
        out.metric("altair_worker_runs_total", "counter", "Ticks per daemon worker.", [({"worker": w.name}, w.runs) for w in rows])
        out.metric("altair_worker_errors_total", "counter", "Failed ticks per daemon worker.", [({"worker": w.name}, w.errors) for w in rows])
        out.metric("altair_worker_last_run_timestamp_seconds", "gauge", "When each worker last ran (Unix time).",
                   [({"worker": w.name}, w.last_run_at) for w in rows])
    return out.text()


def write_file(path: str | Path, text: str) -> None:
    """Atomically, as node_exporter's textfile collector expects."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="utf-8", newline="\n")
    os.replace(partial, path)
