"""`altair doctor` (SPEC §4.1, §7.5, §7.10, §8.2): checks that this PC, the
NAS, S3 and the rigs are set up the way Altair relies on.

Every system probe (PowerShell, the registry, file versions) goes through
:class:`Probe`, so the checks are plain functions that tests drive with a fake
probe. A probe that can't answer (not Windows, not admin, a missing cmdlet)
makes the check ``skip`` or ``warn``, never crash.

Only ``fail`` sets the exit code: an unwritable state folder, an unreachable
rig or NAS, and an S3 archive whose daemon credentials could delete raw data.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import time as dtime
from pathlib import Path
from typing import Any

from altair.catalog.db import Catalog
from altair.config import AltairConfig

GB = 1024 ** 3
REQUIRED_HEADERS = ("FOCALLEN", "GAIN", "OFFSET", "CCD-TEMP")


@dataclass
class Check:
    section: str
    label: str
    status: str            # ok / warn / fail / skip
    detail: str = ""

    def line(self) -> str:
        mark = "FAIL" if self.status == "fail" else self.status   # failures stand out, as in `rigs check`
        return f"[{mark}] {self.label}{': ' + self.detail if self.detail else ''}"


class Probe:
    """Access to Windows facts; everything returns None when unavailable."""

    windows = sys.platform == "win32"

    def powershell(self, script: str, timeout: float = 30) -> str | None:
        if not self.windows:
            return None
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                                    timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    def registry(self, key: str, name: str) -> Any:
        if not self.windows:
            return None
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
                return winreg.QueryValueEx(handle, name)[0]
        except OSError:
            return None

    def file_version(self, path: str) -> str | None:
        if not self.windows or not Path(path).exists():
            return None
        try:
            import win32api

            info = win32api.GetFileVersionInfo(path, "\\")
            ms, ls = info["FileVersionMS"], info["FileVersionLS"]
            return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        except Exception:  # noqa: BLE001 - no version resource
            return None

    def session_id(self) -> int | None:
        from altair import winapi

        return winapi.session_id()

    def long_paths(self) -> bool | None:
        from altair import winapi

        return winapi.long_paths_enabled()


# ── helpers ──────────────────────────────────────────────────────────────
def _hhmm(text: str) -> dtime:
    hours, minutes = (int(x) for x in text.strip().split(":"))
    return dtime(hours, minutes)


def window_inside(window: tuple[int, int], active: tuple[int, int]) -> bool:
    """Is the hour window [start, end) inside active hours [start, end) (both may wrap midnight)?"""
    def hours(start: int, end: int) -> set[int]:
        return set(range(start, end)) if start < end else set(range(start, 24)) | set(range(0, end))
    return hours(*window) <= hours(*active)


def drive_of(path: Path) -> str | None:
    drive = os.path.splitdrive(str(path))[0]
    return drive[0].upper() if len(drive) == 2 and drive[1] == ":" else None


def unc_host(root: str | None) -> str | None:
    m = re.match(r"^(?:\\\\|//)([^\\/]+)[\\/]", root or "")
    return m[1] if m else None


def parse_speed(text: str | None) -> float | None:
    """'2.5 Gbps' / '1 Gbps' / '100 Mbps' → Gbit/s."""
    m = re.match(r"^\s*([\d.]+)\s*([GMK])bps", text or "")
    if not m:
        return None
    return float(m[1]) * {"G": 1, "M": 1e-3, "K": 1e-6}[m[2]]


# ── the checks ───────────────────────────────────────────────────────────
class Doctor:
    def __init__(self, catalog: Catalog | None, config: AltairConfig, *, probe: Probe | None = None, s3: Any = None):
        self.catalog = catalog
        self.config = config
        self.probe = probe or Probe()
        self.s3 = s3
        self.results: list[Check] = []

    def add(self, section: str, label: str, status: str, detail: str = "") -> Check:
        check = Check(section, label, status, detail)
        self.results.append(check)
        return check

    def ok_or(self, section: str, label: str, passed: bool | None, detail: str = "", *, bad: str = "warn") -> Check:
        return self.add(section, label, "skip" if passed is None else "ok" if passed else bad, detail)

    def run(self) -> list[Check]:
        self.pc()
        self.nas()
        self.s3_archive()
        self.rigs()
        return self.results

    # ── this PC (§4.1) ───────────────────────────────────────────────────
    def pc(self) -> None:
        from altair.executor.pixinsight import runner_path

        cfg, s = self.config, "this PC"
        state = Path(cfg.paths.state)
        try:
            state.mkdir(parents=True, exist_ok=True)
            (state / ".doctor").write_text("ok")
            (state / ".doctor").unlink()
            self.add(s, "state folder writable", "ok", str(state))
        except OSError as exc:
            self.add(s, "state folder writable", "fail", f"{state}: {exc}")
        exe = Path(cfg.pixinsight.executable)
        self.ok_or(s, "PixInsight executable", exe.exists(), str(exe))
        self.ok_or(s, "PJSR runner", runner_path(cfg.pixinsight).exists(), str(runner_path(cfg.pixinsight)))
        self.pixinsight_version()
        for name, path in (("work", cfg.paths.work_dir), ("cache", cfg.cache_dir), ("spool", cfg.paths.spool_dir)):
            target = path if path.exists() else path.parent
            try:
                free = shutil.disk_usage(target).free / GB
                self.ok_or(s, f"{name} folder free space", free > 50, f"{path}: {free:.0f} GB free")
            except OSError as exc:
                self.add(s, f"{name} folder", "warn", f"{path}: {exc}")
        self.fast_disks()
        self.defender()
        self.active_hours()
        if self.probe.windows:
            session = self.probe.session_id()
            self.ok_or(s, "interactive session (not a service)", session not in (None, 0), f"session {session}")
            self.ok_or(s, "long paths enabled", self.probe.long_paths(), "HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled")
        for channel in cfg.notifications.channels:
            missing = [channel[k] for k in channel if k.endswith("_env") and channel[k] and not os.environ.get(channel[k])]
            self.ok_or(s, f"notification channel {channel.get('type')}", not missing, f"missing env {', '.join(missing)}" if missing else "")

    def pixinsight_version(self) -> None:
        tested = self.config.pixinsight.tested_versions
        version = self.probe.file_version(self.config.pixinsight.executable)
        source = "file version"
        if version is None and self.catalog is not None:
            row = self.catalog.one("SELECT result_json FROM jobs WHERE status = 'succeeded' AND result_json IS NOT NULL ORDER BY finished_at DESC LIMIT 1")
            version = ((json.loads(row["result_json"]).get("software") or {}).get("pixinsight")) if row else None
            source = "last job"
        if version is None:
            self.add("this PC", "PixInsight version tested", "skip", "unknown until PixInsight has run a job")
            return
        good = any(version == t or version.startswith(t + ".") for t in tested)
        self.ok_or("this PC", "PixInsight version tested", good, f"{version} ({source}); tested: {', '.join(tested)}")

    def fast_disks(self) -> None:
        for name, path in (("work", self.config.paths.work_dir), ("cache", self.config.cache_dir)):
            letter = drive_of(path)
            if letter is None:
                self.add("this PC", f"{name} on a local SSD", "skip" if not self.probe.windows else "warn",
                         f"{path} is not on a local drive letter" if self.probe.windows else "")
                continue
            answer = self.probe.powershell(f"$d = Get-Partition -DriveLetter {letter} | Get-Disk | Get-PhysicalDisk; "
                                           "\"$($d.MediaType)|$($d.BusType)\"")
            if not answer:
                self.add("this PC", f"{name} on a local SSD", "skip", "couldn't read the disk type")
                continue
            media, _, bus = answer.partition("|")
            fast = media.strip().upper() == "SSD" and bus.strip().upper() not in ("USB", "ISCSI")
            self.ok_or("this PC", f"{name} on a local SSD", fast, f"{letter}: {media.strip()} over {bus.strip()} (NVMe recommended)")

    def defender(self) -> None:
        answer = self.probe.powershell("(Get-MpPreference).ExclusionPath -join ';'")
        if answer is None:
            self.add("this PC", "Defender excludes work/cache/spool", "skip", "Get-MpPreference unavailable")
            return
        if "N/A" in answer or "administrator" in answer.lower():
            self.add("this PC", "Defender excludes work/cache/spool", "warn", "run `altair doctor` as administrator to read the exclusions")
            return
        excluded = [e.strip().rstrip("\\/").lower() for e in answer.split(";") if e.strip()]
        missing = []
        for name, path in (("work", self.config.paths.work_dir), ("cache", self.config.cache_dir), ("spool", self.config.paths.spool_dir)):
            p = str(path).replace("/", "\\").rstrip("\\").lower()
            if not any(p == e.replace("/", "\\") or p.startswith(e.replace("/", "\\") + "\\") for e in excluded):
                missing.append(f"{name} ({path})")
        self.ok_or("this PC", "Defender excludes work/cache/spool", not missing,
                   "not excluded: " + ", ".join(missing) + " (Add-MpPreference -ExclusionPath …)" if missing else "")

    def active_hours(self) -> None:
        key = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
        start, end = self.probe.registry(key, "ActiveHoursStart"), self.probe.registry(key, "ActiveHoursEnd")
        window = self.config.pixinsight.processing_window_local
        if start is None or end is None:
            self.add("this PC", "Windows Update active hours cover processing", "skip", "active hours not readable")
            return
        a, b = (_hhmm(t) for t in window.split("-"))
        end_hour = (b.hour + (1 if b.minute else 0)) % 24     # the hour the window has fully left
        inside = window_inside((a.hour, end_hour), (int(start), int(end)))
        self.ok_or("this PC", "Windows Update active hours cover processing", inside,
                   f"active {int(start):02d}:00–{int(end):02d}:00, processing {window}: outside active hours Windows may restart mid-job")

    # ── the NAS (§7.10) ──────────────────────────────────────────────────
    def nas(self) -> None:
        from altair.storage import nas as nas_mod
        from altair.storage.locations import nas_location

        s, loc = "NAS", nas_location(self.config)
        if loc is None:
            self.add(s, "NAS configured", "warn", "storage.locations has no 'nas'")
            return
        if self.catalog is not None:
            health = nas_mod.check(self.catalog, self.config, loc)
            self.ok_or(s, "reachable and healthy", health.reachable and health.healthy, health.reason or str(loc.root), bad="fail")
            if health.free_percent is not None:
                self.ok_or(s, "free space", health.free_percent >= self.config.storage.nas.min_free_percent, f"{health.free_percent:.1f}%")
        host = unc_host(self.config.storage.nas.root)
        if host is None:
            self.add(s, "SMB 3 and link speed", "skip", "the NAS root isn't a UNC path")
            return
        dialect = self.probe.powershell(f"(Get-SmbConnection -ServerName {host} | Select-Object -First 1).Dialect")
        if dialect:
            try:
                major_minor = float(".".join(dialect.strip().split(".")[:2]))   # "3.1.1" → 3.1
                self.ok_or(s, "SMB dialect ≥ 3", major_minor >= 3.0, f"SMB {dialect}")
            except ValueError:
                self.add(s, "SMB dialect ≥ 3", "skip", dialect)
        else:
            self.add(s, "SMB dialect ≥ 3", "skip", "no SMB connection to read (open the share first)")
        speed = self.probe.powershell(f"$ip = (Resolve-DnsName {host} -Type A | Select-Object -First 1).IPAddress; "
                                      "(Get-NetAdapter -InterfaceIndex (Find-NetRoute -RemoteIPAddress $ip | Select-Object -First 1).InterfaceIndex).LinkSpeed")
        gbps = parse_speed(speed)
        if gbps is None:
            self.add(s, "link speed ≥ 2.5 Gbit/s", "skip", "couldn't read the link speed")
        else:
            self.ok_or(s, "link speed ≥ 2.5 Gbit/s", gbps >= 2.5, speed.strip())

    # ── S3 (§7.5) ────────────────────────────────────────────────────────
    def s3_archive(self) -> None:
        from altair.storage import s3_setup

        cfg = self.config.storage.s3
        if cfg is None or self.s3 is None:
            self.add("S3", "S3 configured", "skip", "no S3 location")
            return
        results = s3_setup.check(self.s3.client, cfg)
        for label, passed, detail in results:
            critical = label in ("delete denied under raw/", "bucket versioning enabled")
            self.ok_or("S3", label, passed, detail, bad="fail" if critical else "warn")
        if cfg.object_lock is None:
            self.add("S3", "object lock", "warn", "off: Object Lock (governance) on raw/ and projects/ is strongly recommended (SPEC R16)")
        if self.catalog is not None:
            s3_setup.flag(self.catalog, self.config, results)

    # ── rigs (§4.1, §8.2) and their headers ──────────────────────────────
    def rigs(self) -> None:
        from altair.storage.locations import rig_location

        for name, rig in self.config.rigs.items():
            s = f"rig {name}"
            source = rig_location(self.config, name)
            if source is None:
                self.add(s, "share", "skip", "no raw_root (not collected)")
            else:
                reachable = source.reachable()
                self.ok_or(s, "share reachable", reachable, str(source.root), bad="fail")
                if reachable:
                    cleanup = self.config.rig_cleanup(name).enabled
                    self.ok_or(s, "share writable for cleanup", os.access(source.root, os.W_OK) or not cleanup,
                               "cleanup disabled" if not cleanup else "")
            if rig.rotator.present and rig.rotator.units == "steps" and not rig.rotator.steps_per_revolution:
                self.add(s, "rotator wraps", "warn", "units are steps without steps_per_revolution: positions are compared linearly, "
                                                    "assuming the rotator never wraps (SPEC §8.2)")
            self.headers(name, rig)

    def headers(self, name: str, rig) -> None:
        s = f"rig {name}"
        if self.catalog is None:
            return
        rows = self.catalog.query("SELECT raw_headers_json, file_name FROM frames WHERE rig = ? AND image_type = 'light' AND raw_headers_json IS NOT NULL "
                                  "ORDER BY date_obs DESC LIMIT 20", (name,))
        if not rows:
            self.add(s, "NINA headers", "skip", "no lights collected yet")
            return
        problems: dict[str, int] = {}
        for row in rows:
            header = {k.upper(): v for k, v in json.loads(row["raw_headers_json"] or "{}").items()}
            wanted = list(REQUIRED_HEADERS) + ([k.upper() for k in rig.rotator.keywords] if rig.rotator.present and rig.rotator.source == "header" else [])
            for key in wanted:
                if header.get(key) in (None, ""):
                    problems[f"no {key}"] = problems.get(f"no {key}", 0) + 1
            if self.config.hub.enabled and not re.match(r"^#\d+(\s|$)", str(header.get("OBJECT") or "")):
                problems["OBJECT without #<target id>"] = problems.get("OBJECT without #<target id>", 0) + 1
        aliases = self.catalog.one("SELECT count(*) AS n FROM issues WHERE status = 'open' AND kind = 'UNKNOWN_ALIAS' AND fingerprint LIKE ?",
                                   (f"UNKNOWN_ALIAS:{name}:%",))["n"]
        if aliases:
            problems["telescope/camera names without an alias (UNKNOWN_ALIAS)"] = aliases
        self.ok_or(s, f"NINA headers ({len(rows)} recent lights)", not problems,
                   "; ".join(f"{k}: {n}" for k, n in sorted(problems.items())))


def print_report(results: list[Check], echo) -> None:
    section = None
    for check in results:
        if check.section != section:
            section = check.section
            echo(f"\n{section}")
        echo("  " + check.line())


def as_json(results: list[Check]) -> str:
    return json.dumps([asdict(c) for c in results], indent=1)
