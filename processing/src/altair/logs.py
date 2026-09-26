"""Structured logs (SPEC §13).

Every log record carries the context it was written in: the rig, night,
project, job or issue a worker is busy with, set with :func:`log_context`
(a context variable, so each worker thread has its own). The daemon writes one
JSON object per line to ``state/logs/altaird.jsonl``, rotated at midnight and
kept ``logging.retention_days``; `altair logs` filters and follows it.
PixInsight's own console output stays in ``logs/jobs/<job-id>.log``.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
import time
import traceback
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

from altair.config import AltairConfig

CONTEXT_KEYS = ("worker", "rig", "night", "project_id", "target_id", "filter", "job_id", "kind", "issue")
_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("altair_log_context", default={})
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Add fields to every record logged inside the block (None values are skipped)."""
    token = _context.set({**_context.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_context.get())


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.altair = current_context()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
            **getattr(record, "altair", {}),
        }
        if record.exc_info:
            entry["exc"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return json.dumps(entry, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        ctx = getattr(record, "altair", {})
        return text + (" [" + " ".join(f"{k}={v}" for k, v in ctx.items()) + "]" if ctx else "")


def log_file(config: AltairConfig) -> Path:
    return config.paths.logs_dir / ("altaird.jsonl" if config.logging.format == "json" else "altaird.log")


def setup(config: AltairConfig, *, windowless: bool = False, console: bool = True) -> Path:
    """Logging for `altair serve`: the rotated daemon log, and the console unless windowless."""
    cfg = config.logging
    config.paths.logs_dir.mkdir(parents=True, exist_ok=True)
    path = log_file(config)
    root = logging.getLogger()
    root.setLevel(LEVELS[cfg.level])
    for handler in list(root.handlers):
        if getattr(handler, "_altair", False):
            root.removeHandler(handler)
    file_handler = TimedRotatingFileHandler(path, when="midnight", backupCount=cfg.retention_days, encoding="utf-8", utc=True)
    file_handler.setFormatter(JsonFormatter() if cfg.format == "json" else TextFormatter())
    handlers: list[logging.Handler] = [file_handler]
    if console and not windowless:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(TextFormatter())
        handlers.append(stream)
    for handler in handlers:
        handler._altair = True  # type: ignore[attr-defined]
        handler.addFilter(ContextFilter())
        root.addHandler(handler)
    return path


# ── reading (`altair logs`) ──────────────────────────────────────────────
def files(config: AltairConfig) -> list[Path]:
    """The JSON log and its rotated days, oldest first."""
    current = config.paths.logs_dir / "altaird.jsonl"
    rotated = sorted(config.paths.logs_dir.glob("altaird.jsonl.*"))
    return rotated + ([current] if current.exists() else [])


def matches(entry: dict[str, Any], *, level: str | None = None, since: str | None = None, text: str | None = None,
            **fields: Any) -> bool:
    if level and LEVELS.get(entry.get("level", "INFO"), 20) < LEVELS[level.upper()]:
        return False
    if since and entry.get("ts", "") < since:
        return False
    for key, wanted in fields.items():
        if wanted is not None and str(entry.get(key)) != str(wanted):
            return False
    return not text or text.lower() in (entry.get("msg", "") + entry.get("exc", "")).lower()


def read(paths: list[Path], **filters: Any) -> Iterator[dict[str, Any]]:
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if matches(entry, **filters):
                    yield entry


def follow(path: Path, *, poll_s: float = 1.0, stop=lambda: False, **filters: Any) -> Iterator[dict[str, Any]]:
    """New entries as they are written (reopens the file after midnight rotation)."""
    handle = open(path, encoding="utf-8", errors="replace")
    handle.seek(0, 2)
    inode = path.stat().st_ino
    try:
        while not stop():
            line = handle.readline()
            if line:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if matches(entry, **filters):
                    yield entry
                continue
            time.sleep(poll_s)
            if path.exists() and path.stat().st_ino != inode:
                handle.close()
                handle = open(path, encoding="utf-8", errors="replace")
                inode = path.stat().st_ino
    finally:
        handle.close()


def format_entry(entry: dict[str, Any]) -> str:
    ctx = " ".join(f"{k}={entry[k]}" for k in CONTEXT_KEYS if k in entry)
    line = f"{entry.get('ts', '')} {entry.get('level', ''):<7} {entry.get('logger', '')}: {entry.get('msg', '')}"
    line += f"  [{ctx}]" if ctx else ""
    return line + ("\n" + entry["exc"] if entry.get("exc") else "")
