"""The optional local status endpoint (SPEC §12.2): read-only, for Home
Assistant, dashboards and Prometheus. It uses only the standard library and
runs as a daemon thread of altaird.

| Path | Returns |
|---|---|
| `/` | an index page |
| `/status` | the ALTAIR_STATUS.json document |
| `/issues?open=1&kind=K` | issues |
| `/projects/<id>` | a project, its night masters and multi-night versions |
| `/jobs?status=S&limit=N` | jobs, newest first |
| `/healthz` | 200 when every worker ran recently, else 503 |
| `/metrics` | Prometheus text format |
| `/reports/<kind>/<id>` | a night or merge report as HTML (`.md` for the Markdown) |

It binds to 127.0.0.1 by default. On any other address it refuses to start
without a token (``http.token_env``), then expects ``Authorization: Bearer
<token>`` or ``?token=<token>``.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from altair import markdown, metrics, status_page
from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.reports import report_path

log = logging.getLogger("altair.http")
REPORT_KINDS = ("night_master", "provisional_noflat", "multi_night_master")
PAGE = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>@TITLE@</title>"
        "<style>" + status_page.CSS + "</style></head><body>@BODY@</body></html>")


def page(title: str, body: str) -> str:
    return PAGE.replace("@TITLE@", title).replace("@BODY@", body)


class ConfigError(Exception):
    pass


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class StatusServer:
    def __init__(self, catalog: Catalog, config: AltairConfig, *, health: Callable[[], dict[str, Any]] | None = None,
                 workers: Callable[[], list] | None = None):
        cfg = config.http
        self.token = os.environ.get(cfg.token_env) if cfg.token_env else None
        if not _loopback(cfg.bind) and not self.token:
            raise ConfigError(f"http.bind is {cfg.bind}: set http.token_env (and that variable) to serve beyond this PC")
        self.catalog = catalog
        self.config = config
        self.health = health or (lambda: {"ok": True, "workers": {}})
        self.workers = workers
        self.httpd = ThreadingHTTPServer((cfg.bind, cfg.port), self._handler())
        self.httpd.daemon_threads = True
        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def start(self) -> threading.Thread:
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="altaird-http", daemon=True)
        self.thread.start()
        log.info("status endpoint on http://%s:%s/", self.config.http.bind, self.port)
        return self.thread

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # ── routes ───────────────────────────────────────────────────────────
    def route(self, path: str, query: dict[str, list[str]]) -> tuple[int, str, str | bytes]:
        q = {k: v[-1] for k, v in query.items()}
        parts = [p for p in path.split("/") if p]
        if not parts:
            return 200, "text/html; charset=utf-8", self._index()
        head = parts[0]
        if head == "status" and len(parts) == 1:
            return self._json(status_page.build(self.catalog, self.config))
        if head == "issues" and len(parts) == 1:
            clauses, args = ["1 = 1"], []
            if q.get("open") in ("1", "true"):
                clauses.append("status = 'open'")
            if q.get("kind"):
                clauses.append("kind = ?")
                args.append(q["kind"].upper())
            rows = self.catalog.query(f"SELECT id, kind, severity, status, fingerprint, message, scope_json, created_at, resolved_at, resolution "
                                      f"FROM issues WHERE {' AND '.join(clauses)} ORDER BY id DESC", tuple(args))
            return self._json([{**{k: r[k] for k in r.keys() if k != "scope_json"}, "scope": json.loads(r["scope_json"])} for r in rows])
        if head == "jobs" and len(parts) == 1:
            limit = min(int(q.get("limit", 100)), 1000)
            rows = self.catalog.query("SELECT id, kind, status, rig, night, filter, project_id, attempts, created_at, started_at, finished_at, "
                                      "waiting_reason, error FROM jobs" + (" WHERE status = ?" if q.get("status") else "")
                                      + " ORDER BY id DESC LIMIT ?", ((q["status"], limit) if q.get("status") else (limit,)))
            return self._json([dict(r) for r in rows])
        if head == "projects" and len(parts) == 2 and parts[1].isdigit():
            return self._project(int(parts[1]))
        if head == "healthz" and len(parts) == 1:
            state = self.health()
            return (200 if state.get("ok") else 503), "application/json", json.dumps(state)
        if head == "metrics" and len(parts) == 1:
            return 200, "text/plain; version=0.0.4; charset=utf-8", metrics.collect(self.catalog, self.config, self.workers)
        if head == "reports" and len(parts) == 3 and parts[1] in REPORT_KINDS:
            raw = parts[2].endswith(".md")
            ident = parts[2][:-3] if raw else parts[2]
            if ident.isdigit():
                path = report_path(self.config, parts[1], int(ident))
                if path.exists():
                    text = path.read_text(encoding="utf-8")
                    if raw:
                        return 200, "text/markdown; charset=utf-8", text
                    return 200, "text/html; charset=utf-8", page("Altair report", markdown.render(text))
        return 404, "application/json", json.dumps({"error": "not found"})

    def _json(self, value: Any) -> tuple[int, str, str]:
        return 200, "application/json", json.dumps(value, default=str)

    def _project(self, project_id: int) -> tuple[int, str, str]:
        project = self.catalog.one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if project is None:
            return 404, "application/json", json.dumps({"error": "no such project"})
        nights = self.catalog.query("SELECT id, night, filter, kind, merge_status, merge_block_reason, n_frames, total_exposure_s, sha256 "
                                    "FROM night_masters WHERE project_id = ? AND superseded_by IS NULL ORDER BY night", (project_id,))
        merges = self.catalog.query("SELECT id, filter, version, n_nights, total_exposure_s, created_at, sha256, inputs_json, excluded_json "
                                    "FROM multi_night_masters WHERE project_id = ? ORDER BY filter, version", (project_id,))

        def with_report(kind: str, row) -> dict:
            out = {k: row[k] for k in row.keys() if not k.endswith("_json")}
            if report_path(self.config, kind, row["id"]).exists():
                out["report"] = f"/reports/{kind}/{row['id']}"
            return out
        body = {k: project[k] for k in ("id", "target", "telescope", "camera", "rig", "hub_target_id", "hub_project_id", "reference_version",
                                        "reference_night", "multi_night_mode", "path")}
        body["nights"] = [with_report("night_master" if n["kind"] == "final" else "provisional_noflat", n) for n in nights]
        body["multi_night"] = [{**with_report("multi_night_master", m), "inputs": json.loads(m["inputs_json"]),
                                "excluded": json.loads(m["excluded_json"])} for m in merges]
        return self._json(body)

    def _index(self) -> str:
        links = "".join(f"<li><a href='{p}'>{p}</a> {d}</li>" for p, d in (
            ("/status", "everything on the status page, as JSON"), ("/issues?open=1", "open issues"), ("/jobs", "the job queue"),
            ("/healthz", "daemon workers"), ("/metrics", "Prometheus metrics")))
        reports = "".join(f"<li><a href='/reports/{r['kind']}/{r['id']}'>{r['label']}</a></li>" for r in self._recent_reports())
        return page("Altair", f"<h1>Altair</h1><ul>{links}</ul><h2>Recent reports</h2><ul>{reports or '<li>none</li>'}</ul>")

    def _recent_reports(self) -> list[dict]:
        out = []
        for m in self.catalog.query("SELECT m.id, m.filter, m.version, p.target FROM multi_night_masters m JOIN projects p ON p.id = m.project_id "
                                    "ORDER BY m.id DESC LIMIT 10"):
            if report_path(self.config, "multi_night_master", m["id"]).exists():
                out.append({"kind": "multi_night_master", "id": m["id"], "label": f"{m['target']} {m['filter']} v{m['version']}"})
        for n in self.catalog.query("SELECT n.id, n.kind, n.night, n.filter, p.target FROM night_masters n JOIN projects p ON p.id = n.project_id "
                                    "WHERE n.superseded_by IS NULL ORDER BY n.id DESC LIMIT 20"):
            kind = "night_master" if n["kind"] == "final" else "provisional_noflat"
            if report_path(self.config, kind, n["id"]).exists():
                out.append({"kind": kind, "id": n["id"], "label": f"{n['target']} {n['filter']} {n['night']}"})
        return out

    # ── plumbing ─────────────────────────────────────────────────────────
    def _authorized(self, handler: BaseHTTPRequestHandler, query: dict[str, list[str]]) -> bool:
        if not self.token:
            return True
        header = handler.headers.get("Authorization", "")
        given = header[7:] if header.startswith("Bearer ") else (query.get("token") or [""])[-1]
        return hmac.compare_digest(given.encode(), self.token.encode())

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "altair-status"

            def do_GET(self):  # noqa: N802 - http.server's naming
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if not server._authorized(self, query):
                    return self._send(HTTPStatus.UNAUTHORIZED, "application/json", json.dumps({"error": "token required"}))
                try:
                    status, content_type, body = server.route(url.path, query)
                except Exception as exc:  # noqa: BLE001 - one bad request never stops the server
                    log.exception("GET %s failed", url.path)
                    status, content_type, body = 500, "application/json", json.dumps({"error": str(exc)})
                self._send(status, content_type, body)

            def do_HEAD(self):  # noqa: N802
                self.do_GET()

            def _send(self, status: int, content_type: str, body: str | bytes) -> None:
                data = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            def log_message(self, fmt, *args):
                log.debug("http %s " + fmt, self.client_address[0], *args)

        return Handler
