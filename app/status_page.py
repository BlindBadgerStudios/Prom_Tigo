"""Tigo /status page: HTML for operators and JSON for scripts.

The HTTP app delegates every path other than ``/status`` to the Prometheus
WSGI app, so ``/metrics`` stays the scrape endpoint.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from html import escape
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from prometheus_client.exposition import make_wsgi_app

from . import __version__
from .collector import CollectorStatusSnapshot
from .errors import status_label
from .status_health import (
    STATE_RULE_SUMMARY,
    classify_overall,
    docker3_access_note,
    format_ago,
    format_compact,
    format_in,
    format_timestamp_iso,
    format_timestamp_pt,
    hint_for,
    one_line,
    overall_phrase,
    process_phrase,
)

EXPORTER_NAME = "prom_tigo"
CLOUD_TEMPS_NOTE = "Omitted in cloud mode (not an error)"

SeriesCounter = Callable[[], int]
UpReader = Callable[[], float | None]


def connection_mode_detail(mode: str) -> str:
    """Cloud API is live; local CCA is firmware-locked."""
    if mode == "local":
        return "CCA local (firmware-locked)"
    return "Tigo cloud API (live)"


def describe_exception(exc: BaseException) -> tuple[str, str]:
    """Public error code and one-line message. No URL, token, or body dump."""
    code = status_label(exc)
    response = getattr(exc, "response", None)
    reason = getattr(response, "reason", None) if response is not None else None
    if reason:
        message = one_line(str(reason))
    else:
        message = one_line(str(exc) or type(exc).__name__)
    return code, message or code


def count_series(registry: Any) -> int:
    total = 0
    for metric in registry.collect():
        total += len(metric.samples)
    return total


class ExporterStatus:
    """Poll-loop facts the status page reads. Safe for the HTTP threads."""

    def __init__(
        self,
        *,
        mode: str,
        poll_interval_seconds: float,
        listen_port: int,
        exporter_up: UpReader,
        series_count: SeriesCounter,
        version: str = __version__,
        exporter: str = EXPORTER_NAME,
        build: str | None = None,
    ) -> None:
        self.mode = mode
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.listen_port = int(listen_port)
        self.version = version
        self.exporter = exporter
        self.build = build
        self._exporter_up = exporter_up
        self._series_count = series_count
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._poll_thread: threading.Thread | None = None
        self._last_success_at: float | None = None
        self._consecutive_failures = 0
        self._last_error_code: str | None = None
        self._last_error_message: str | None = None
        self._upstream_code: str | None = None
        self._upstream_message: str | None = None
        self._backoff_s: float | None = None
        self._next_retry_at: float | None = None
        self._next_poll_at: float | None = None
        self._extras: CollectorStatusSnapshot | None = None
        self._extras_stale = False
        self._saved_series: int | None = None
        self._series_stale = False

    def attach_poll_thread(self, thread: threading.Thread) -> None:
        self._poll_thread = thread

    def poll_thread_alive(self) -> bool:
        thread = self._poll_thread
        return thread is not None and thread.is_alive()

    def record_success(
        self,
        *,
        delay: float,
        extras: CollectorStatusSnapshot | None,
        now: float | None = None,
    ) -> None:
        moment = time.time() if now is None else now
        series = self._series_count()
        with self._lock:
            self._last_success_at = moment
            self._consecutive_failures = 0
            self._last_error_code = None
            self._last_error_message = None
            self._upstream_code = "200"
            self._upstream_message = "OK"
            self._backoff_s = None
            self._next_retry_at = None
            self._next_poll_at = moment + delay
            self._extras_stale = False
            if extras is not None:
                self._extras = extras
            self._saved_series = series
            self._series_stale = False

    def record_failure(
        self,
        *,
        code: str,
        message: str,
        consecutive_failures: int,
        delay: float,
        now: float | None = None,
    ) -> None:
        moment = time.time() if now is None else now
        safe_code = one_line(code, limit=40) or "error"
        safe_message = one_line(message)
        with self._lock:
            self._consecutive_failures = consecutive_failures
            self._last_error_code = safe_code
            self._last_error_message = safe_message
            self._upstream_code = safe_code
            self._upstream_message = safe_message
            self._backoff_s = delay
            self._next_retry_at = moment + delay
            self._next_poll_at = None
            if self._extras is not None:
                self._extras_stale = True
            if self._saved_series is not None:
                self._series_stale = True

    def payload(self, now: float | None = None) -> dict[str, Any]:
        moment = time.time() if now is None else now
        exporter_up = int(self._exporter_up() or 0)
        alive = self.poll_thread_alive()
        uptime_s = int(max(0.0, time.monotonic() - self._started))
        live_series = None
        with self._lock:
            last_success_at = self._last_success_at
            failures = self._consecutive_failures
            error_code = self._last_error_code
            error_message = self._last_error_message
            upstream_code = self._upstream_code
            upstream_message = self._upstream_message
            backoff_s = self._backoff_s
            next_retry_at = self._next_retry_at
            next_poll_at = self._next_poll_at
            extras = self._extras
            extras_stale = self._extras_stale
            saved_series = self._saved_series
            series_stale = self._series_stale
        if saved_series is None:
            live_series = self._series_count()
        age = None if last_success_at is None else max(0.0, moment - last_success_at)
        overall = classify_overall(
            now=moment,
            poll_interval_seconds=self.poll_interval_seconds,
            last_success_at=last_success_at,
            consecutive_failures=failures,
            last_error_code=error_code,
            poll_thread_alive=alive,
        )
        cloud = self.mode != "local"
        interval = self.poll_interval_seconds
        if interval == int(interval):
            interval = int(interval)
        if failures > 0 and next_retry_at is not None:
            next_poll_relative = "held for backoff"
        elif next_poll_at is not None:
            next_poll_relative = format_in(next_poll_at - moment)
        else:
            next_poll_relative = None
        return {
            "overall": overall,
            "overall_phrase": overall_phrase(overall, age, poll_thread_alive=alive),
            "exporter": self.exporter,
            "version": self.version,
            "build": self.build,
            "mode": self.mode,
            "mode_detail": connection_mode_detail(self.mode),
            "poll_interval_s": interval,
            "next_poll_at": format_timestamp_iso(next_poll_at),
            "next_poll_at_pt": format_timestamp_pt(next_poll_at),
            "next_poll_relative": next_poll_relative,
            "last_success_at": format_timestamp_iso(last_success_at),
            "last_success_at_pt": format_timestamp_pt(last_success_at),
            "last_success_relative": None if age is None else format_ago(age),
            "age_seconds": None if age is None else int(age),
            "upstream": _code_message(upstream_code, upstream_message),
            "failures": failures,
            "backoff_s": backoff_s,
            "next_retry_at": format_timestamp_iso(next_retry_at),
            "next_retry_at_pt": format_timestamp_pt(next_retry_at),
            "next_retry_relative": (
                None if next_retry_at is None else format_in(next_retry_at - moment)
            ),
            "last_error": _code_message(error_code, error_message),
            "hint": hint_for(
                overall=overall,
                error_code=error_code,
                cloud_temps_omitted=cloud,
            ),
            "exporter_up": exporter_up,
            "series_count": saved_series if saved_series is not None else live_series,
            "series_stale": series_stale,
            "extras": _extras_payload(extras, stale=extras_stale, cloud=cloud),
            "uptime_s": uptime_s,
            "process_healthy": alive,
            "poll_thread_alive": alive,
            "process": process_phrase(poll_thread_alive=alive, overall=overall),
            "access_note": docker3_access_note(self.listen_port),
        }


def _code_message(code: str | None, message: str | None) -> dict[str, str] | None:
    if code is None and message is None:
        return None
    return {"code": code or "", "message": message or ""}


def _extras_payload(
    extras: CollectorStatusSnapshot | None, *, stale: bool, cloud: bool
) -> dict[str, Any]:
    if extras is None:
        snapshot = "none"
        present = None
        expected = None
        site_name = None
    else:
        snapshot = "last-known" if stale else "live"
        present = extras.panels_present
        expected = extras.panels_expected
        site_name = extras.site_name
    if present is None or expected is None:
        panels_note = "unknown until next success"
    else:
        panels_note = f"{present} present / {expected} expected"
        if stale:
            panels_note = f"last-known {panels_note}"
    return {
        "site_name": site_name,
        "panels_present": present,
        "panels_expected": expected,
        "panels_note": panels_note,
        "snapshot": snapshot,
        "cloud_temps_omitted": cloud,
        "cloud_temps_note": CLOUD_TEMPS_NOTE if cloud else None,
    }


def render_status_html(payload: dict[str, Any]) -> str:
    """Read-only operator page. Every dynamic value is escaped."""
    overall = str(payload.get("overall") or "DOWN")
    extras = payload.get("extras") or {}
    rows = [
        ("Overall", str(payload.get("overall_phrase") or overall)),
        ("Exporter", _exporter_line(payload)),
        ("Mode", str(payload.get("mode_detail") or "")),
        (
            "Last success",
            _joined_time(
                payload.get("last_success_at_pt"), payload.get("last_success_relative")
            ),
        ),
        ("Poll", _poll_line(payload)),
        ("Upstream", _result_line(payload.get("upstream"))),
        ("Failures", _failures_line(payload.get("failures"))),
        ("Backoff", _backoff_line(payload.get("backoff_s"))),
        ("Next retry", _retry_line(payload)),
        ("Last error", _result_line(payload.get("last_error")) or "None"),
        ("Hint", str(payload.get("hint") or "")),
        ("exporter_up", str(payload.get("exporter_up"))),
        ("Series", _series_line(payload)),
        ("Uptime", format_in_uptime(payload.get("uptime_s"))),
        ("Process", str(payload.get("process") or "")),
        ("Site", _site_line(extras)),
        ("Panels", str(extras.get("panels_note") or "")),
        ("Temps", _temps_line(extras)),
    ]
    body_rows = "\n".join(
        f"      <dt>{escape(label)}</dt><dd>{escape(value)}</dd>"
        for label, value in rows
    )
    title = escape(f"{payload.get('exporter')} status")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font: 16px/1.45 sans-serif; margin: 2rem; color: #1c1c1c; background: #fff; }}
    h1 {{ font-size: 1.35rem; margin-bottom: 0.25rem; }}
    .note {{ max-width: 46rem; }}
    .banner {{ display: inline-block; padding: 0.4rem 0.7rem; font-weight: 700; }}
    .UP {{ background: #e7f6ea; }}
    .DEGRADED {{ background: #fff3d6; }}
    .DOWN {{ background: #fde8e8; }}
    dl {{ display: grid; grid-template-columns: 11rem 1fr; gap: 0.35rem 1rem; max-width: 52rem; }}
    dt {{ color: #444; }}
    dd {{ margin: 0; }}
    footer {{ margin-top: 2rem; max-width: 46rem; color: #333; }}
    a {{ color: #143d66; }}
  </style>
</head>
<body>
  <p class="note">{escape(str(payload.get("access_note") or ""))}</p>
  <h1>{escape(str(payload.get("exporter") or "exporter"))} /status</h1>
  <p class="banner {escape(overall)}">{escape(str(payload.get("overall_phrase") or overall))}</p>
  <dl>
{body_rows}
  </dl>
  <p><a href="?format=json">JSON</a></p>
  <footer>
    <p>{escape(STATE_RULE_SUMMARY)}</p>
    <p>Read-only. This page does not change the exporter or the upstream API.</p>
  </footer>
</body>
</html>
"""


def format_in_uptime(seconds: Any) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    return format_compact(value)


def _exporter_line(payload: dict[str, Any]) -> str:
    line = f"{payload.get('exporter')} {payload.get('version')}"
    build = payload.get("build")
    if build:
        line = f"{line} ({build})"
    return line


def _joined_time(absolute: Any, relative: Any) -> str:
    if not absolute and not relative:
        return "none yet"
    if absolute and relative:
        return f"{absolute} · {relative}"
    return str(absolute or relative)


def _poll_line(payload: dict[str, Any]) -> str:
    interval = payload.get("poll_interval_s")
    relative = payload.get("next_poll_relative") or "n/a"
    absolute = payload.get("next_poll_at_pt")
    when = f"{absolute} · {relative}" if absolute else str(relative)
    return f"every {interval}s · next {when}"


def _retry_line(payload: dict[str, Any]) -> str:
    absolute = payload.get("next_retry_at_pt")
    relative = payload.get("next_retry_relative")
    if not absolute and not relative:
        return "n/a"
    if absolute and relative:
        return f"{absolute} · {relative}"
    return str(absolute or relative)


def _result_line(result: Any) -> str:
    if not isinstance(result, dict):
        return "None"
    code = result.get("code") or ""
    message = result.get("message") or ""
    if code and message:
        return f"{code} · {message}"
    return str(code or message or "None")


def _failures_line(failures: Any) -> str:
    count = int(failures or 0)
    if count == 1:
        return "1 consecutive"
    if count > 1:
        return f"{count} consecutive"
    return "0"


def _backoff_line(backoff_s: Any) -> str:
    if backoff_s is None:
        return "None"
    return format_compact(float(backoff_s))


def _series_line(payload: dict[str, Any]) -> str:
    count = payload.get("series_count")
    text = "unknown" if count is None else str(count)
    if payload.get("series_stale"):
        return f"stale · last-known {text}"
    return text


def _site_line(extras: dict[str, Any]) -> str:
    name = extras.get("site_name")
    if not name:
        return "unknown until next success"
    if extras.get("snapshot") == "last-known":
        return f"last-known {name}"
    return str(name)


def _temps_line(extras: dict[str, Any]) -> str:
    note = extras.get("cloud_temps_note")
    if note:
        return str(note)
    return "polled only when the local CCA returns them"


def make_exporter_wsgi_app(registry: Any, status: ExporterStatus) -> Callable:
    """WSGI app: ``/status`` plus the unchanged Prometheus metrics app."""
    metrics_app = make_wsgi_app(registry)

    def app(environ: dict, start_response: Callable) -> list[bytes]:
        path = environ.get("PATH_INFO", "") or ""
        if path.rstrip("/") == "/status":
            return _status_wsgi(environ, start_response, status)
        return metrics_app(environ, start_response)

    return app


def _status_wsgi(
    environ: dict, start_response: Callable, status: ExporterStatus
) -> list[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method not in {"GET", "HEAD"}:
        body = b"method not allowed\n"
        start_response(
            "405 Method Not Allowed",
            [
                ("Allow", "GET, HEAD"),
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [b"" if method == "HEAD" else body]

    params = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=False)
    requested = (params.get("format") or [""])[0].lower()
    payload = status.payload()
    if requested == "json":
        body = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        content_type = "application/json; charset=utf-8"
    else:
        body = render_status_html(payload).encode("utf-8")
        content_type = "text/html; charset=utf-8"
    start_response(
        "200 OK",
        [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ],
    )
    if method == "HEAD":
        return [b""]
    return [body]


def _listen_family(addr: str, port: int) -> tuple[socket.AddressFamily, str]:
    infos = socket.getaddrinfo(
        addr, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )
    family, _, _, _, sockaddr = next(iter(infos))
    return family, sockaddr[0]


def start_exporter_http_server(
    port: int,
    registry: Any,
    status: ExporterStatus,
    *,
    addr: str = "0.0.0.0",
) -> WSGIServer:
    """Serve /metrics and /status. Same bind behavior as the Prometheus server."""

    class _SilentHandler(WSGIRequestHandler):
        def log_message(self, *_args: Any) -> None:
            return

    class _Server(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    _Server.address_family, bind_addr = _listen_family(addr, port)
    httpd = make_server(
        bind_addr,
        port,
        make_exporter_wsgi_app(registry, status),
        _Server,
        handler_class=_SilentHandler,
    )
    thread = threading.Thread(
        target=httpd.serve_forever, name="exporter-http", daemon=True
    )
    thread.start()
    return httpd
