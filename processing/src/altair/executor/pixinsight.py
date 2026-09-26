"""Starting PixInsight for one job (SPEC §6.5).

One process per job::

    PixInsight.exe -n=<slot> --automation-mode --no-startup-scripts --force-exit -r=<runner>,<job.json>

On Windows the process starts without a window, at below-normal priority,
inside a Job Object with ``KILL_ON_JOB_CLOSE``: closing the job handle on a
timeout kills the whole process tree. Console output goes to the job's log.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from altair.config import PixInsight

log = logging.getLogger("altair.pixinsight")
IS_WINDOWS = sys.platform == "win32"
DEFAULT_RUNNER = Path(__file__).resolve().parents[1] / "pjsr" / "altair_runner.js"


@dataclass
class RunOutcome:
    returncode: int | None
    timed_out: bool
    seconds: float


def runner_path(cfg: PixInsight) -> Path:
    return Path(cfg.runner) if cfg.runner else DEFAULT_RUNNER


def command(cfg: PixInsight, job_json: Path) -> list[str]:
    return [cfg.executable, f"-n={cfg.instance_slot}", *cfg.extra_args, f"-r={runner_path(cfg)},{job_json}"]


def _creationflags(priority: str) -> int:
    if not IS_WINDOWS:
        return 0
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    flags |= {"below_normal": subprocess.BELOW_NORMAL_PRIORITY_CLASS, "idle": subprocess.IDLE_PRIORITY_CLASS}.get(priority, 0)
    return flags


class _JobObject:
    """A Windows Job Object that kills every process in it when closed."""

    def __init__(self):
        self.handle = None
        if not IS_WINDOWS:
            return
        try:
            import win32job

            self.handle = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation, info)
        except Exception as exc:  # noqa: BLE001 - without pywin32 the timeout still kills the main process
            log.warning("no Job Object (%s); a timeout kills only the main process", exc)
            self.handle = None

    def assign(self, proc: subprocess.Popen) -> None:
        if self.handle is None:
            return
        import win32api
        import win32con
        import win32job

        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid)
        win32job.AssignProcessToJobObject(self.handle, handle)

    def close(self) -> None:
        if self.handle is not None:
            self.handle.Close()
            self.handle = None


def run(cmd: list[str], log_path: Path, *, timeout_s: float, priority: str = "below_normal", cwd: Path | None = None) -> RunOutcome:
    """Run to completion or until the timeout; never raises for a failed run."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    job = _JobObject()
    with open(log_path, "ab") as out:
        out.write(f"$ {subprocess.list2cmdline(cmd)}\n".encode())
        out.flush()
        try:
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=cwd,
                                    creationflags=_creationflags(priority))
        except OSError as exc:
            out.write(f"could not start: {exc}\n".encode())
            return RunOutcome(None, False, 0.0)
        try:
            job.assign(proc)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not assign PixInsight to a Job Object: %s", exc)
        try:
            code = proc.wait(timeout=timeout_s)
            return RunOutcome(code, False, time.monotonic() - started)
        except subprocess.TimeoutExpired:
            out.write(f"timeout after {timeout_s:.0f}s, killing\n".encode())
            job.close()   # kills the whole tree on Windows
            proc.kill()
            proc.wait()
            return RunOutcome(proc.returncode, True, time.monotonic() - started)
        finally:
            job.close()


def clear_stale_locks(cfg: PixInsight) -> list[Path]:
    """After a killed run, PixInsight can leave its instance-slot lock behind
    (``%LOCALAPPDATA%/PixInsight/*.lock``-style files mentioning the slot)."""
    removed = []
    if not IS_WINDOWS:
        return removed
    import os

    base = Path(os.environ.get("LOCALAPPDATA", "")) / "PixInsight"
    for lock in base.glob(f"*{cfg.instance_slot}*.lock") if base.exists() else []:
        try:
            lock.unlink()
            removed.append(lock)
        except OSError:
            pass
    return removed
