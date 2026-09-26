"""The local status endpoint, metrics and the report renderer (SPEC §12.2, §13)."""
from __future__ import annotations

import re

import httpx
import pytest

from altair import markdown, metrics
from altair.http_status import ConfigError, StatusServer

from test_reports import processed_night


@pytest.fixture
def served(tmp_path):
    config, catalog = processed_night(tmp_path)
    config = config.model_copy(update={"http": config.http.model_copy(update={"enabled": True, "port": 0})})
    server = StatusServer(catalog, config, health=lambda: {"ok": False, "workers": {"collect:esprit": {"ok": False}}})
    server.start()
    yield httpx.Client(base_url=f"http://127.0.0.1:{server.port}"), catalog, config
    server.stop()


def test_json_endpoints(served):
    client, catalog, config = served
    status = client.get("/status").json()
    assert status["projects"][0]["multi_night"][0]["version"] == 1
    assert client.get("/issues", params={"open": 1}).json() == []
    jobs = client.get("/jobs", params={"status": "succeeded", "limit": 2}).json()
    assert len(jobs) == 2 and jobs[0]["id"] > jobs[1]["id"]
    project = client.get("/projects/1").json()
    assert project["nights"][0]["report"] == "/reports/night_master/1" and project["multi_night"][0]["inputs"][0]["night"] == "2026-09-24"
    assert client.get("/projects/99").status_code == 404 and client.get("/nope").status_code == 404
    health = client.get("/healthz")
    assert health.status_code == 503 and health.json()["workers"]["collect:esprit"] == {"ok": False}
    assert "Recent reports" in client.get("/").text


def test_reports_render_as_html(served):
    client, catalog, config = served
    page = client.get("/reports/night_master/1")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "<h1>T34 · Ha · night 2026-09-24</h1>" in page.text
    assert '<th style="text-align: right">Weight</th>' in page.text and re.search(r'<td style="text-align: left">light_\d+\.fits</td>', page.text)
    assert client.get("/reports/night_master/1.md").text.startswith("# T34")
    assert client.get("/reports/multi_night_master/1").status_code == 200
    assert client.get("/reports/night_master/9").status_code == 404 and client.get("/reports/frames/1").status_code == 404


def test_metrics(served):
    client, catalog, config = served
    body = client.get("/metrics").text
    assert '# TYPE altair_jobs gauge' in body and 'altair_jobs{status="succeeded"} 6' in body
    assert "altair_raw_lights_without_s3 6" in body   # no S3 configured here and 'altair_issues_open' in body
    assert 'altair_job_last_duration_seconds{kind="MERGE"}' in body
    assert "altair_hub_outbox_pending 0" in body and 'altair_info{version="' in body
    path = config.paths.state + "/metrics.prom"
    metrics.write_file(path, body)
    assert open(path).read() == body


def test_a_token_is_required_beyond_this_pc(tmp_path, monkeypatch):
    config, catalog = processed_night(tmp_path)
    public = config.model_copy(update={"http": config.http.model_copy(update={"enabled": True, "port": 0, "bind": "0.0.0.0"})})
    with pytest.raises(ConfigError, match="token_env"):
        StatusServer(catalog, public)
    monkeypatch.setenv("ALTAIR_HTTP_TOKEN", "s3cret")
    public = public.model_copy(update={"http": public.http.model_copy(update={"token_env": "ALTAIR_HTTP_TOKEN"})})
    server = StatusServer(catalog, public)
    server.start()
    try:
        client = httpx.Client(base_url=f"http://127.0.0.1:{server.port}")
        assert client.get("/status").status_code == 401
        assert client.get("/status", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert client.get("/metrics", params={"token": "s3cret"}).status_code == 200
        assert client.get("/status", headers={"Authorization": "Bearer nope"}).status_code == 401
    finally:
        server.stop()


def test_markdown_renderer_escapes_html():
    html = markdown.render("# Title <b>x</b>\n\nText with **bold**, `code` and [a link](https://example.org) and [bad](javascript:x).\n\n"
                           "| A | B |\n| :--- | ---: |\n| 1 \\| 2 | <script>alert(1)</script> |\n\n- one\n- two\n\n![map](cov.jpg)\n")
    assert "<h1>Title &lt;b&gt;x&lt;/b&gt;</h1>" in html
    assert "<strong>bold</strong>" in html and "<code>code</code>" in html
    assert '<a href="https://example.org" rel="noopener nofollow">a link</a>' in html and "javascript:" not in html.split("[bad]")[0] + "x"
    assert '<td style="text-align: left">1 | 2</td>' in html and "&lt;script&gt;" in html and "<script>" not in html
    assert "<ul><li>one</li><li>two</li></ul>" in html and '<img alt="map" src="cov.jpg">' in html
