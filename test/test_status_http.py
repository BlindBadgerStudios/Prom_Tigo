"""HTTP /status JSON and HTML, last-known Tigo extras, and unchanged /metrics."""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.request
from pathlib import Path

import requests
from prometheus_client.exposition import make_wsgi_app

from app import __version__
from app.collector import CollectorStatusSnapshot
from app.main import LoopState, _run_cycle
from app.metrics import build_metrics
from app.status_page import (
    ExporterStatus,
    describe_exception,
    make_exporter_wsgi_app,
    render_status_html,
    start_exporter_http_server,
)

REQUIRED_FIELDS = {
    "overall",
    "overall_phrase",
    "exporter",
    "version",
    "build",
    "mode",
    "mode_detail",
    "poll_interval_s",
    "next_poll_at",
    "next_poll_at_pt",
    "next_poll_relative",
    "last_success_at",
    "last_success_at_pt",
    "last_success_relative",
    "age_seconds",
    "upstream",
    "failures",
    "backoff_s",
    "next_retry_at",
    "next_retry_at_pt",
    "next_retry_relative",
    "last_error",
    "hint",
    "exporter_up",
    "series_count",
    "series_stale",
    "extras",
    "uptime_s",
    "process_healthy",
    "poll_thread_alive",
    "process",
    "access_note",
}
EXTRA_FIELDS = {
    "site_name",
    "panels_present",
    "panels_expected",
    "panels_note",
    "snapshot",
    "cloud_temps_omitted",
    "cloud_temps_note",
}
FORBIDDEN_KEYS = {
    "latitude",
    "longitude",
    "lat",
    "lon",
    "password",
    "passwd",
    "token",
    "authorization",
    "email",
    "street",
    "gps",
    "address",
    "cookie",
}


class MaxRng(random.Random):
    def uniform(self, a, b):
        return b


class ScriptedCollector:
    def __init__(self, failures=()):
        self.failures = list(failures)
        self.logins = 0
        self.status_extras = None

    def login(self):
        self.logins += 1

    def collect_once(self):
        if self.failures:
            raise self.failures.pop(0)


def http_error(status: int, *, text: str | None = None, reason: str | None = None):
    response = requests.Response()
    response.status_code = status
    if reason is not None:
        response.reason = reason
    message = text or f"HTTP {status}"
    return requests.exceptions.HTTPError(message, response=response)


def _alive() -> threading.Thread:
    thread = threading.Thread(target=threading.Event().wait, daemon=True)
    thread.start()
    return thread


def _status(
    metrics=None, *, mode: str = "cloud", build: str | None = None
) -> ExporterStatus:
    metrics = metrics or build_metrics()
    status = ExporterStatus(
        mode=mode,
        poll_interval_seconds=60,
        listen_port=10111,
        exporter_up=lambda: metrics.registry.get_sample_value("tigo_exporter_up"),
        series_count=lambda: 4,
        version=__version__,
        build=build,
    )
    status.attach_poll_thread(_alive())
    return status


def _wsgi(app, path: str, query: str = "", method: str = "GET"):
    captured: dict = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(
        app(
            {
                "REQUEST_METHOD": method,
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "10111",
                "SERVER_PROTOCOL": "HTTP/1.1",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": None,
                "wsgi.errors": None,
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
                "HTTP_ACCEPT": "text/plain",
            },
            start_response,
        )
    )
    return captured["status"], captured["headers"], body


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key).lower()
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_version_matches_pyproject_and_changelog():
    project = Path("pyproject.toml").read_text()
    changelog = Path("CHANGELOG.md").read_text()
    assert f'version = "{__version__}"' in project
    assert f"## {__version__} " in changelog


def test_up_json_and_html_share_fields():
    now = 1_762_000_000.0
    snap = CollectorStatusSnapshot("<Orchard & array>", 51, 51)
    status = _status(build="abc1234")
    status.record_success(delay=300, extras=snap, now=now - 47)
    payload = status.payload(now=now)

    assert set(payload) == REQUIRED_FIELDS
    assert set(payload["extras"]) == EXTRA_FIELDS
    assert payload["overall"] == "UP"
    assert payload["overall_phrase"] == "UP"
    assert payload["exporter"] == "prom_tigo"
    assert payload["version"] == __version__
    assert payload["build"] == "abc1234"
    assert payload["mode"] == "cloud"
    assert payload["mode_detail"] == "Tigo cloud API (live)"
    assert payload["poll_interval_s"] == 60
    assert payload["last_success_relative"] == "47s ago"
    assert payload["age_seconds"] == 47
    assert payload["last_success_at_pt"]
    assert payload["next_poll_at_pt"]
    assert payload["next_poll_relative"]
    assert payload["upstream"] == {"code": "200", "message": "OK"}
    assert payload["failures"] == 0
    assert payload["backoff_s"] is None
    assert payload["next_retry_at"] is None
    assert payload["last_error"] is None
    assert payload["process"] == "Healthy"
    assert payload["process_healthy"] is True
    assert "upstream empty, not scrape down" in payload["hint"]
    assert "omitted in cloud mode (not an error)" in payload["hint"]
    assert payload["extras"]["panels_note"] == "51 present / 51 expected"
    assert payload["extras"]["snapshot"] == "live"
    assert payload["extras"]["cloud_temps_omitted"] is True
    assert payload["access_note"] == (
        "Reachable on docker3 as http://127.0.0.1:10111/status "
        "via lab SSH hop (no public listen)."
    )
    assert FORBIDDEN_KEYS.isdisjoint(_keys(payload))

    html = render_status_html(payload)
    assert payload["access_note"] in html
    assert payload["overall_phrase"] in html
    assert payload["last_success_at_pt"] in html
    assert "47s ago" in html
    assert payload["next_poll_at_pt"] in html
    assert "51 present / 51 expected" in html
    assert "Omitted in cloud mode (not an error)" in html
    assert "prom_tigo 0.5.1 (abc1234)" in html
    assert "<Orchard & array>" not in html
    assert "&lt;Orchard &amp; array&gt;" in html
    assert "<form" not in html.lower()
    assert "2× the poll interval" in html


def test_down_and_degraded_keep_last_known_panels_and_retry_times():
    now = 1_762_000_000.0
    snap = CollectorStatusSnapshot("Orchard array", 51, 51)
    status = _status()
    status.record_success(delay=60, extras=snap, now=now - 39 * 60)
    status.record_failure(
        code="503",
        message="Service Unavailable token=supersecret",
        consecutive_failures=8,
        delay=12 * 60,
        now=now,
    )
    payload = status.payload(now=now)
    html = render_status_html(payload)

    assert payload["overall"] == "DEGRADED"
    assert payload["overall_phrase"] == "DEGRADED — no success in 39m"
    assert payload["extras"]["snapshot"] == "last-known"
    assert payload["extras"]["site_name"] == "Orchard array"
    assert payload["extras"]["panels_present"] == 51
    assert payload["extras"]["panels_expected"] == 51
    assert payload["extras"]["panels_note"] == "last-known 51 present / 51 expected"
    assert payload["series_stale"] is True
    assert payload["next_retry_at_pt"]
    assert payload["next_retry_relative"] == "~12m"
    assert payload["next_poll_relative"] == "held for backoff"
    assert payload["last_error"]["code"] == "503"
    assert "supersecret" not in payload["last_error"]["message"]
    assert "check docker3 outbound / upstream API" in payload["hint"]
    assert "Don't restart-loop" in payload["hint"]
    assert payload["next_retry_at_pt"] in html
    assert "~12m" in html
    assert "last-known Orchard array" in html
    assert "supersecret" not in html
    assert "supersecret" not in json.dumps(payload)

    auth_status = _status()
    auth_status.record_success(delay=60, extras=snap, now=now - 17 * 60)
    auth_status.record_failure(
        code="401",
        message="Unauthorized",
        consecutive_failures=4,
        delay=6 * 60,
        now=now,
    )
    auth = auth_status.payload(now=now)
    assert auth["overall"] == "DOWN"
    assert auth["overall_phrase"] == "DOWN — no success in 17m"
    assert auth["last_success_relative"] == "17m ago"
    assert auth["next_retry_relative"] == "~6m"
    assert "check API token/cred mount; do not paste into chat" in auth["hint"]
    assert auth["extras"]["snapshot"] == "last-known"
    assert "17m ago" in render_status_html(auth)
    assert auth["next_retry_at_pt"] in render_status_html(auth)


def test_local_mode_does_not_call_missing_cloud_temps_an_error():
    status = _status(mode="local")
    payload = status.payload()
    assert payload["mode_detail"] == "CCA local (firmware-locked)"
    assert payload["extras"]["cloud_temps_omitted"] is False
    assert payload["extras"]["cloud_temps_note"] is None
    assert payload["extras"]["panels_note"] == "unknown until next success"
    assert "omitted in cloud mode" not in payload["hint"]
    assert "firmware-locked" in render_status_html(payload)


def test_wsgi_status_routes_and_metrics_match_prometheus():
    metrics = build_metrics()
    metrics.exporter_up.set(1)
    status = _status(metrics)
    status.record_success(
        delay=60,
        extras=CollectorStatusSnapshot("Orchard array", 51, 51),
        now=time.time() - 10,
    )
    app = make_exporter_wsgi_app(metrics.registry, status)
    stock = make_wsgi_app(metrics.registry)

    for path in ("/metrics", "/anything-else"):
        assert _wsgi(app, path) == _wsgi(stock, path)

    html_status, html_headers, html_body = _wsgi(app, "/status")
    assert html_status == "200 OK"
    assert html_headers["Content-Type"].startswith("text/html")
    page = html_body.decode()
    assert "http://127.0.0.1:10111/status" in page
    assert "lab SSH hop" in page
    assert "no public listen" in page
    assert "tigo_exporter_up" not in page

    json_status, json_headers, json_body = _wsgi(app, "/status", "format=JSON")
    assert json_status == "200 OK"
    assert json_headers["Content-Type"].startswith("application/json")
    loaded = json.loads(json_body)
    assert set(loaded) == REQUIRED_FIELDS
    assert loaded["extras"]["panels_present"] == 51
    assert loaded["overall"] == "UP"

    denied, _, _ = _wsgi(app, "/status", method="POST")
    assert denied == "405 Method Not Allowed"

    metrics_status, metrics_headers, metrics_body = _wsgi(app, "/metrics")
    assert metrics_status == "200 OK"
    assert "text/plain" in metrics_headers["Content-Type"]
    text = metrics_body.decode()
    assert "tigo_exporter_up" in text
    assert "lab SSH hop" not in text
    assert "<html" not in text.lower()


def test_describe_exception_redacts_tokenized_urls():
    exc = http_error(
        401,
        reason="Unauthorized",
        text=(
            "401 Client Error: Unauthorized for url: "
            "https://api.example.test/v3?token=supersecret&lat=45.5123 "
            "owner@example.com"
        ),
    )
    code, message = describe_exception(exc)
    assert code == "401"
    assert message == "Unauthorized"
    assert "supersecret" not in message

    leaked = describe_exception(
        RuntimeError(
            "boom token=supersecret password=hunter2 owner@example.com " + ("x" * 400)
        )
    )
    assert "supersecret" not in leaked[1]
    assert "hunter2" not in leaked[1]
    assert "owner@example.com" not in leaked[1]
    assert len(leaked[1]) <= 200


def test_run_cycle_publishes_success_and_keeps_snapshot_after_failure():
    metrics = build_metrics()
    snap = CollectorStatusSnapshot("Orchard array", 51, 51)
    collector = ScriptedCollector()
    collector.status_extras = snap
    status = _status(metrics)
    state = LoopState()

    delay = _run_cycle(collector, 60, metrics, state, rng=MaxRng(), status_page=status)
    assert delay == 60.0
    assert metrics.registry.get_sample_value("tigo_exporter_up") == 1.0
    up = status.payload()
    assert up["overall"] == "UP"
    assert up["exporter_up"] == 1
    assert up["extras"]["panels_note"] == "51 present / 51 expected"
    assert up["series_stale"] is False

    collector.failures.append(
        http_error(502, reason="Bad Gateway", text="502 token=supersecret")
    )
    failed_delay = _run_cycle(
        collector, 60, metrics, state, rng=MaxRng(), status_page=status
    )
    assert failed_delay == 60.0
    assert metrics.registry.get_sample_value("tigo_exporter_up") == 0.0
    down = status.payload()
    assert down["overall"] == "DEGRADED"
    assert down["exporter_up"] == 0
    assert down["extras"]["snapshot"] == "last-known"
    assert down["extras"]["site_name"] == "Orchard array"
    assert down["last_error"]["code"] == "502"
    assert "supersecret" not in json.dumps(down)
    assert "tigo_exporter_up" in {
        sample.name
        for metric in metrics.registry.collect()
        for sample in metric.samples
    }


def test_live_server_serves_status_and_metrics():
    metrics = build_metrics()
    status = _status(metrics)
    status.record_success(
        delay=60,
        extras=CollectorStatusSnapshot("Orchard array", 51, 51),
        now=time.time() - 5,
    )
    server = start_exporter_http_server(0, metrics.registry, status)
    port = server.server_address[1]
    try:
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/status").read().decode()
        loaded = json.load(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/status?format=json")
        )
        metrics_body = (
            urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics").read().decode()
        )
    finally:
        server.shutdown()
        server.server_close()

    assert "http://127.0.0.1:10111/status" in html
    assert "via lab SSH hop (no public listen)" in html
    assert loaded["exporter"] == "prom_tigo"
    assert loaded["extras"]["panels_expected"] == 51
    assert "tigo_exporter_up" in metrics_body
    assert "lab SSH hop" not in metrics_body
