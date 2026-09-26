"""The executor (SPEC §6.5): runs queued jobs through PixInsight, one at a
time on the configured instance slot.

For each job, in priority order (calibration masters, then references, then
night stacks, then merges) and once its dependencies have succeeded:

1. Resolve its inputs: every light and sub by SHA-256, and every master or
   reference by SHA-256 or by the job that builds it.
2. Stage them (§7.7). Anything not yet readable puts the job in
   ``waiting_data``; it never holds the PixInsight slot.
3. Check the work disk has room (about 3 × the input size × drizzle²).
4. Write ``job.json``, run PixInsight, read ``result.json``.
5. Publish the outputs (§6.6). A publish intent is recorded first, so a crash
   during publishing publishes again from the work directory on restart
   instead of running PixInsight again.

Failures retry with back-off up to ``max_attempts``; then the job fails and
``JOB_FAILED`` is raised. A MERGE runs in two phases: measure every night
master in one pass, compute the night weights, then integrate.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from altair import __version__, winapi
from altair.catalog.db import Catalog, now_iso
from altair.config import AltairConfig
from altair.executor import pixinsight
from altair.executor.contract import ContractError, RunResult, read_result, write_job
from altair.hub.reporters import enqueue_job
from altair.planner.plan import plan_hash, resolve_plan
from altair.issues import raise_issue, resolve_issue
from altair.projects import weights as weights_mod
from altair.publish.publisher import Publisher, VerifyError
from altair.storage.stager import Stager

log = logging.getLogger("altair.executor")
PRIORITY = {"CALIB_MASTER": 0, "PROJECT_REFERENCE": 1, "NIGHT_STACK": 2, "MERGE": 3}
ACTIVE = ("queued", "staging", "waiting_data", "running")
DEAD = ("failed", "blocked", "skipped", "superseded")
WAIT_RETRY_S = 600
DISK_FACTOR = 3
GB = 1024 ** 3


@dataclass
class JobReport:
    job_id: int
    kind: str
    status: str
    detail: str | None = None


class JobFailure(Exception):
    def __init__(self, message: str, *, retry: bool = True):
        super().__init__(message)
        self.retry = retry


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class Executor:
    def __init__(self, catalog: Catalog, config: AltairConfig, *, stager: Stager | None = None, publisher: Publisher | None = None,
                 run: Callable[..., pixinsight.RunOutcome] = pixinsight.run, clock: Callable[[], datetime] | None = None,
                 keep_work_dirs: bool = False):
        self.catalog = catalog
        self.config = config
        self.stager = stager or Stager(catalog, config)
        self.publisher = publisher or Publisher(catalog, config)
        self.run_process = run
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.keep_work_dirs = keep_work_dirs

    # ── bookkeeping ──────────────────────────────────────────────────────
    def _set(self, job_id: int, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.catalog.transaction() as tx:
            tx.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
            if "status" in fields and self.config.hub.enabled:
                enqueue_job(tx, job_id)

    def work_dir(self, job_id: int) -> Path:
        return self.config.paths.work_dir / str(job_id)

    def log_path(self, job_id: int) -> Path:
        return self.config.paths.logs_dir / "jobs" / f"{job_id}.log"

    # ── recovery (§4.1: the service restarts after a crash or reboot) ────
    def recover(self) -> list[int]:
        """Jobs left ``running``/``staging`` by a crash: publish again when
        the run had finished, otherwise queue them again."""
        recovered = []
        for job in self.catalog.query("SELECT * FROM jobs WHERE status IN ('running', 'staging')"):
            intent = self.catalog.one("SELECT * FROM publish_intents WHERE job_id = ?", (job["id"],))
            if intent and (Path(intent["work_dir"]) / "result.json").exists():
                try:
                    result = read_result(Path(intent["work_dir"]))
                    self._finish(job, result, Path(intent["work_dir"]))
                    recovered.append(job["id"])
                    continue
                except Exception as exc:  # noqa: BLE001 - fall back to running it again
                    log.warning("re-publishing job %s failed (%s); it runs again", job["id"], exc)
            self.catalog.execute("DELETE FROM publish_intents WHERE job_id = ?", (job["id"],))
            self._set(job["id"], status="queued", waiting_reason="recovered after a restart")
            recovered.append(job["id"])
        return recovered

    # ── choosing the next job ────────────────────────────────────────────
    def _deps(self, job: sqlite3.Row) -> tuple[str, str | None]:
        """('ready' | 'waiting' | 'dead', reason)."""
        ids = json.loads(job["depends_on_json"] or "[]")
        if not ids:
            return "ready", None
        rows = self.catalog.query(f"SELECT id, kind, status FROM jobs WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
        for row in rows:
            if row["status"] in DEAD:
                return "dead", f"depends on {row['kind']} job {row['id']}, which is {row['status']}"
        if all(r["status"] == "succeeded" for r in rows) and len(rows) == len(ids):
            return "ready", None
        return "waiting", None

    def selection(self, where: Callable[[sqlite3.Row], bool]) -> set[int]:
        """Ids of the jobs ``where`` accepts, plus every job they depend on."""
        rows = {r["id"]: r for r in self.catalog.query("SELECT * FROM jobs")}
        chosen = {i for i, r in rows.items() if where(r)}
        todo = list(chosen)
        while todo:
            for dep in json.loads(rows[todo.pop()]["depends_on_json"] or "[]"):
                if dep in rows and dep not in chosen:
                    chosen.add(dep)
                    todo.append(dep)
        return chosen

    def candidates(self, only: set[int] | None = None) -> list[sqlite3.Row]:
        now = self.clock()
        self._unblock()
        rows = self.catalog.query("SELECT * FROM jobs WHERE status IN ('queued', 'waiting_data') ORDER BY id")
        out = []
        for job in rows:
            if only is not None and job["id"] not in only:
                continue
            not_before = _ts(job["not_before"])
            if not_before and not_before > now:
                continue
            state, reason = self._deps(job)
            if state == "dead":
                self._set(job["id"], status="blocked", waiting_reason=reason)
                continue
            if state == "ready":
                out.append(job)
        return sorted(out, key=lambda j: (PRIORITY.get(j["kind"], 9), j["id"]))

    def _unblock(self) -> None:
        """A job blocked by a dependency runs once the dependency succeeds (after a rerun)."""
        for job in self.catalog.query("SELECT * FROM jobs WHERE status = 'blocked' AND waiting_reason LIKE 'depends on %'"):
            if self._deps(job)[0] != "dead":
                self._set(job["id"], status="queued", waiting_reason=None)

    def pending(self) -> int:
        return self.catalog.one(f"SELECT count(*) AS n FROM jobs WHERE status IN ({','.join('?' * len(ACTIVE))})", ACTIVE)["n"]

    # ── running ──────────────────────────────────────────────────────────
    def run_once(self) -> JobReport | None:
        for job in self.candidates():
            report = self.execute(job)
            if report.status != "waiting_data":
                return report
        return None

    def run_all(self, *, max_jobs: int | None = None, where: Callable[[sqlite3.Row], bool] | None = None) -> list[JobReport]:
        """Run until nothing is runnable (``altair run``); keeps Windows awake
        meanwhile. ``where`` limits it to some jobs (and what they depend on);
        jobs queued by those runs (e.g. a merge after a stack) are re-selected."""
        reports = []
        winapi.keep_awake(True)
        try:
            tried: set[int] = set()
            while max_jobs is None or len(reports) < max_jobs:
                only = self.selection(where) if where else None
                runnable = [j for j in self.candidates(only) if j["id"] not in tried]
                if not runnable:
                    break
                job = runnable[0]
                report = self.execute(job)
                reports.append(report)
                if report.status in ("waiting_data", "queued"):
                    tried.add(job["id"])   # retried on a later pass, not in a loop now
        finally:
            winapi.keep_awake(False)
        return reports

    def execute(self, job: sqlite3.Row) -> JobReport:
        job_id = job["id"]
        plan = json.loads(job["plan_json"])
        self._set(job_id, status="staging", started_at=job["started_at"] or now_iso())
        try:
            refs = self.resolve_refs(plan)
            resolved = plan_hash(resolve_plan(plan, self._ref_sha))
        except JobFailure as exc:
            return self._wait(job, str(exc))
        if resolved != job["plan_hash"]:
            self._set(job_id, resolved_hash=resolved)
        inputs = sorted(set(plan.get("input_paths", {})) | set(refs.values()))
        staged = self.stager.stage(job_id, inputs, allow_s3_fallback=False)
        if not staged.ready:
            return self._wait(job, staged.waiting_reason or "waiting for data")
        work = self.work_dir(job_id)
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        if (reason := self._disk_guard(job, plan, inputs, work)) is not None:
            return self._wait(job, reason)
        names = {sha: Path(p).name for sha, p in plan.get("input_paths", {}).items()}
        paths = self.stager.handoff(work, staged.paths, names)
        self._set(job_id, status="running", waiting_reason=None, attempts=(job["attempts"] or 0) + 1, log_path=str(self.log_path(job_id)))
        try:
            if job["kind"] == "MERGE":
                result = self._run_merge(job, plan, refs, paths, work)
            else:
                result = self._run(job, self.job_document(job, plan, refs, paths, work), work)
            return self._finish(job, result, work)
        except JobFailure as exc:
            return self._fail(job, str(exc), retry=exc.retry)
        except VerifyError as exc:
            return self._fail(job, f"verification failed: {exc}", retry=False)

    def _wait(self, job: sqlite3.Row, reason: str) -> JobReport:
        not_before = (self.clock() + timedelta(seconds=WAIT_RETRY_S)).isoformat().replace("+00:00", "Z")
        self._set(job["id"], status="waiting_data", waiting_reason=reason, not_before=not_before)
        return JobReport(job["id"], job["kind"], "waiting_data", reason)

    def _disk_guard(self, job, plan: dict, inputs: list[str], work: Path) -> str | None:
        if not inputs:
            return None
        marks = ",".join("?" * len(inputs))
        size = self.catalog.one(f"SELECT coalesce(sum(size_bytes), 0) AS n FROM blobs WHERE sha256 IN ({marks})", tuple(inputs))["n"]
        drizzle = int(plan.get("drizzle_scale") or 1)
        need = DISK_FACTOR * size * drizzle * drizzle
        free = shutil.disk_usage(work).free
        fingerprint = f"DISK_SPACE_LOW:job:{job['id']}"
        with self.catalog.transaction() as tx:
            if free < need:
                raise_issue(tx, self.config, kind="DISK_SPACE_LOW", severity="blocking", fingerprint=fingerprint,
                            message=f"Job {job['id']} needs about {need / GB:.1f} GB of work space in {self.config.paths.work_dir}, "
                                    f"{free / GB:.1f} GB is free. It waits until there is room.", scope={"job_id": job["id"]})
                return "not enough free space in the work directory"
            resolve_issue(tx, self.config, fingerprint, resolution="auto:space")
        return None

    # ── references between jobs ──────────────────────────────────────────
    def _ref_sha(self, ref: dict | None, role: str) -> str | None:
        if not ref:
            return None
        if ref.get("sha256"):
            return ref["sha256"]
        row = self.catalog.one("SELECT status, result_json FROM jobs WHERE plan_hash = ?", (ref["job"],))
        if row is None or row["status"] != "succeeded" or not row["result_json"]:
            raise JobFailure(f"waiting for the job that builds its {role}")
        return json.loads(row["result_json"])["registered"][role]

    def resolve_refs(self, plan: dict) -> dict[str, str]:
        """Every master/reference a plan refers to, as {slot: sha256}."""
        refs: dict[str, str] = {}
        for kind, ref in (plan.get("calibrate_with") or {}).items():
            refs[f"calibrate_with.{kind}"] = self._ref_sha(ref, "master")
        for i, group in enumerate(plan.get("groups") or []):
            for key in ("dark", "flat"):
                if group.get(key):
                    refs[f"groups.{i}.{key}"] = self._ref_sha(group[key], "master")
        if plan.get("reference"):
            refs["reference"] = self._ref_sha(plan["reference"], "reference")
        return {k: v for k, v in refs.items() if v}

    # ── job.json ─────────────────────────────────────────────────────────
    def job_document(self, job: sqlite3.Row, plan: dict, refs: dict[str, str], paths: dict[str, str], work: Path,
                     **extra: Any) -> dict:
        def ref(slot: str) -> dict | None:
            sha = refs.get(slot)
            return {"sha256": sha, "path": paths.get(sha)} if sha else None

        doc: dict[str, Any] = {"job_id": job["id"], "kind": job["kind"], "work_dir": str(work), "output_dir": str(work / "out"),
                               "altair_version": __version__, "plan_hash": job["plan_hash"],
                               "pixinsight": {"wbpp_dir": self.config.pixinsight.wbpp_dir, "engine": self.config.pixinsight.night_stack_engine}}
        if job["kind"] == "CALIB_MASTER":
            doc["master"] = plan["master"]
            doc["frames"] = [{"sha256": s, "path": paths[s]} for s in plan["inputs"]]
            doc["calibrate_with"] = {k: ref(f"calibrate_with.{k}") for k in plan.get("calibrate_with") or {}}
        elif job["kind"] in ("NIGHT_STACK", "PROJECT_REFERENCE"):
            doc.update({k: plan.get(k) for k in ("project_id", "rig", "night", "filter", "filters", "stack_kind", "drizzle_scale", "version",
                                                 "keep_calibrated_frames", "reference_version")})
            profile = plan.get("wbpp_profile") or self.config.night_processing.wbpp_profile
            doc["wbpp_profile"] = {"name": profile, **((self.config.wbpp.get("profiles") or {}).get(profile) or {})}
            rig = self.config.rigs.get(plan.get("rig") or "")
            cam = self.config.camera(rig.camera) if rig else None
            doc["camera"] = {"type": cam.type if cam else "mono", "bayer_pattern": cam.bayer_pattern if cam else None}
            doc["groups"] = [{"filter": g.get("filter", plan.get("filter")), "exposure": g.get("exposure"), "rotator_pos": g.get("rotator_pos"),
                              "lights": [{"sha256": s, "path": paths[s]} for s in g["lights"]],
                              "dark": ref(f"groups.{i}.dark"), "flat": ref(f"groups.{i}.flat")}
                             for i, g in enumerate(plan["groups"])]
            doc["reference"] = ref("reference")
            doc["grouping_keywords"] = ["TELESCOP", "INSTRUME", "FILTER", "OBJECT", "ROTATOR"]
        doc.update(extra)
        return doc

    def _run(self, job: sqlite3.Row, doc: dict, work: Path, phase: str | None = None) -> RunResult:
        run_dir = work / phase if phase else work
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "out").mkdir(exist_ok=True)
        doc = {**doc, "work_dir": str(run_dir), "output_dir": str(run_dir / "out"), **({"phase": phase} if phase else {})}
        job_json = write_job(run_dir / "job.json", doc)
        cfg = self.config.pixinsight
        started = time.monotonic()
        outcome = self.run_process(pixinsight.command(cfg, job_json), self.log_path(job["id"]),
                                   timeout_s=cfg.timeout_minutes * 60, priority=cfg.priority)
        if outcome.timed_out:
            pixinsight.clear_stale_locks(cfg)
            raise JobFailure(f"PixInsight timed out after {cfg.timeout_minutes:g} min")
        try:
            result = read_result(run_dir)
        except ContractError as exc:
            raise JobFailure(f"{exc} (exit code {outcome.returncode}); see {self.log_path(job['id'])}") from exc
        if result.status != "ok":
            raise JobFailure(f"PixInsight: {result.error or 'error'}")
        if outcome.returncode not in (0, None):
            raise JobFailure(f"PixInsight exited with code {outcome.returncode}")
        log.info("job %s %s%s ran in %.0fs", job["id"], job["kind"], f" ({phase})" if phase else "", time.monotonic() - started)
        return result

    def _run_merge(self, job: sqlite3.Row, plan: dict, refs: dict[str, str], paths: dict[str, str], work: Path) -> RunResult:
        base = {"job_id": job["id"], "kind": "MERGE", "altair_version": __version__, "plan_hash": job["plan_hash"],
                **{k: plan[k] for k in ("project_id", "filter", "mode", "weighting", "normalization", "rejection", "min_coverage_nights",
                                        "autocrop", "drizzle_scale")},
                "reference": {"sha256": refs.get("reference"), "path": paths.get(refs.get("reference"))},
                "nights": [{**{k: n[k] for k in ("night", "sha256", "frames", "exposure_s")}, "path": paths[n["sha256"]]} for n in plan["nights"]],
                "calibrated_frames": [{"sha256": s, "path": paths[s]} for s in plan.get("calibrated_frames", [])]}
        if plan["mode"] == "frame_reintegration":
            return self._run_reintegration(job, plan, base, paths, work)
        measured = self._run(job, base, work, "measure") if plan["weighting"] != "frame_weight_sum" else None
        try:
            weights = weights_mod.compute(plan["weighting"], plan["nights"], measured.measurements if measured else [])
        except weights_mod.WeightError as exc:
            raise JobFailure(str(exc), retry=False) from exc
        plan["weights"] = weights
        integrate = {**base, "weights": weights, "normalization_reference": weights_mod.normalization_reference(weights)}
        self._set(job["id"], plan_json=json.dumps(plan, sort_keys=True))
        return self._run(job, integrate, work, "integrate")

    def _run_reintegration(self, job: sqlite3.Row, plan: dict, base: dict, paths: dict[str, str], work: Path) -> RunResult:
        """SPEC §9.6: every calibrated sub of every eligible night, registered
        again to the same reference, normalized and integrated in one pass;
        ImageIntegration weighs the frames itself (PSF signal weight). The
        nights' contributions are the sums of their frames' weights."""
        of_night = {sha: n["sha256"] for n in plan["nights"] for sha in n.get("calibrated", [])}
        doc = {**base, "calibrated_frames": [{"sha256": s, "path": paths[s], "night_sha256": of_night.get(s)}
                                             for s in plan.get("calibrated_frames", [])]}
        result = self._run(job, doc, work, "integrate")
        weights = {k: float(v) for k, v in (result.metrics.get("night_weights") or {}).items() if v}
        if set(weights) != {n["sha256"] for n in plan["nights"]}:
            weights = {n["sha256"]: float(n.get("frames") or 1) for n in plan["nights"]}   # the runner didn't report them
        plan["weights"] = weights
        self._set(job["id"], plan_json=json.dumps(plan, sort_keys=True))
        return result

    # ── outcome ──────────────────────────────────────────────────────────
    def _finish(self, job: sqlite3.Row, result: RunResult, work: Path) -> JobReport:
        with self.catalog.transaction() as tx:
            tx.execute("INSERT OR REPLACE INTO publish_intents(job_id, work_dir, created_at) VALUES (?, ?, ?)", (job["id"], str(work), now_iso()))
        job = self.catalog.one("SELECT * FROM jobs WHERE id = ?", (job["id"],))
        registered = self.publisher.publish(job, result, work)
        stored = {"registered": registered, "metrics": result.metrics, "software": result.software}
        with self.catalog.transaction() as tx:
            tx.execute("UPDATE jobs SET status = 'succeeded', finished_at = ?, error = NULL, waiting_reason = NULL, result_json = ? WHERE id = ?",
                       (now_iso(), json.dumps(stored, default=str), job["id"]))
            tx.execute("DELETE FROM publish_intents WHERE job_id = ?", (job["id"],))
            resolve_issue(tx, self.config, f"JOB_FAILED:{job['id']}", resolution="auto:succeeded")
            if self.config.hub.enabled:
                enqueue_job(tx, job["id"])
        if not self.keep_work_dirs:
            shutil.rmtree(work, ignore_errors=True)
        return JobReport(job["id"], job["kind"], "succeeded")

    def _fail(self, job: sqlite3.Row, error: str, *, retry: bool) -> JobReport:
        attempts = (self.catalog.one("SELECT attempts FROM jobs WHERE id = ?", (job["id"],))["attempts"] or 0)
        if retry and attempts < self.config.pixinsight.max_attempts:
            backoff = 300 * 2 ** (attempts - 1)
            not_before = (self.clock() + timedelta(seconds=backoff)).isoformat().replace("+00:00", "Z")
            self._set(job["id"], status="queued", error=error, not_before=not_before)
            log.warning("job %s failed (attempt %s), retrying in %ss: %s", job["id"], attempts, backoff, error)
            return JobReport(job["id"], job["kind"], "queued", error)
        scope = json.loads(job["scope_json"] or "{}")
        with self.catalog.transaction() as tx:
            tx.execute("UPDATE jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?", (error, now_iso(), job["id"]))
            raise_issue(tx, self.config, kind="JOB_FAILED", severity="blocking", fingerprint=f"JOB_FAILED:{job['id']}",
                        message=f"{job['kind']} job {job['id']} ({scope.get('night') or ''} {scope.get('filter') or ''}) failed after "
                                f"{attempts} attempt(s): {error}. The work directory {self.work_dir(job['id'])} and the log "
                                f"{self.log_path(job['id'])} are kept; `altair rerun --job {job['id']}` tries again.",
                        scope={**scope, "job_id": job["id"]})
            if self.config.hub.enabled:
                enqueue_job(tx, job["id"])
        return JobReport(job["id"], job["kind"], "failed", error)

    # ── housekeeping ─────────────────────────────────────────────────────
    def cleanup(self, *, failed_days: float = 14, log_days: float = 90) -> dict:
        """Work directories of failed jobs and old job logs go after a while."""
        now = time.time()
        removed = {"work_dirs": 0, "logs": 0}
        work_root = self.config.paths.work_dir
        if work_root.exists():
            active = {str(r["id"]) for r in self.catalog.query(f"SELECT id FROM jobs WHERE status IN ({','.join('?' * len(ACTIVE))})", ACTIVE)}
            for d in work_root.iterdir():
                if d.is_dir() and d.name not in active and now - d.stat().st_mtime > failed_days * 86400:
                    shutil.rmtree(d, ignore_errors=True)
                    removed["work_dirs"] += 1
        logs = self.config.paths.logs_dir / "jobs"
        if logs.exists():
            for f in logs.glob("*.log"):
                if now - f.stat().st_mtime > log_days * 86400:
                    f.unlink(missing_ok=True)
                    removed["logs"] += 1
        return removed

    def rerun(self, job_id: int) -> bool:
        row = self.catalog.one("SELECT status FROM jobs WHERE id = ?", (job_id,))
        if row is None or row["status"] in ("running", "staging"):
            return False
        with self.catalog.transaction() as tx:
            tx.execute("UPDATE jobs SET status = 'queued', attempts = 0, error = NULL, not_before = NULL, waiting_reason = NULL WHERE id = ?",
                       (job_id,))
            resolve_issue(tx, self.config, f"JOB_FAILED:{job_id}", resolution="rerun")
            if self.config.hub.enabled:
                enqueue_job(tx, job_id)
        return True
