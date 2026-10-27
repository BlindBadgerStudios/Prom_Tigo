"""Overall state for the energy Prometheus exporters.

This module is the shared rule for prom_tigo, prom_solark, and prom_emvue.
Copy it into each repo. Do not let the states drift.

Decision order in :func:`classify_overall`:

1. Poll thread not alive → **DOWN**.
2. Latest poll failed with HTTP 401 or 403 → **DOWN** (hard auth failure).
3. Latest poll succeeded and the success is inside the healthy window → **UP**.
4. Latest poll failed with an upstream availability problem (HTTP 5xx, 408,
   429, timeout, connection, TLS, or DNS) and the poll thread is alive →
   **DEGRADED**. This stays DEGRADED after the hard age cutoff. The process
   is still backing off on purpose; operators must not restart-loop it.
5. No successful poll yet, or the last success is older than the hard age
   cutoff → **DOWN**.
6. Anything else while the thread is alive (stale success inside the hard
   cutoff, or a non-availability error such as HTTP 400) → **DEGRADED**.

Windows (tunable constants below):

- Healthy window = 2 × poll interval. A success older than that is not UP.
- Hard age cutoff = 15 minutes, or 3 × poll interval when that is longer.
  The longer-of choice keeps a slow poll cadence from being marked DOWN
  between successful polls. Availability failures in step 4 do not use
  this cutoff.

Age phrases look like ``DOWN — no success in 17m`` and ``DEGRADED — no
success in 39m``. UP has no age phrase.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HEALTHY_WINDOW_POLL_MULTIPLIER = 2
HARD_AGE_SECONDS = 15 * 60
HARD_AGE_POLL_MULTIPLIER = 3

# Latest-poll codes that stay DEGRADED while the process keeps polling.
UPSTREAM_DEGRADED_NAMES = frozenset(
    {
        "timeout",
        "Timeout",
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutError",
        "ConnectionError",
        "ConnectTimeoutError",
        "NewConnectionError",
        "SSLError",
        "ProxyError",
        "ChunkedEncodingError",
        "ProtocolError",
        "OSError",
        "gaierror",
    }
)
AUTH_CODES = frozenset({"401", "403"})
MESSAGE_LIMIT = 200

_PT = "America/Los_Angeles"

_SECRET_QUERY = re.compile(
    r"([?&#](?:access_token|refresh_token|id_token|token|password|passwd|pwd|"
    r"secret|api_key|apikey|key|auth|authorization|session|sid|cookie|"
    r"signature|sig|lat|lon|latitude|longitude|lng|gps)=)[^&#\s]+",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|password|passwd|pwd|token|secret|api_key|apikey|"
    r"cookie|set-cookie|lat|lon|latitude|longitude|lng|gps|address|street)"
    r"\b(\s*[:=]\s*)(?:bearer\s+)?\S+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]+")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_ENV_SECRET = re.compile(
    r"(?i)([A-Za-z0-9_]*(?:password|passwd|pwd|token|secret|api_key|apikey))"
    r"(\s*=\s*)\S+"
)
_WS = re.compile(r"\s+")


def healthy_window_seconds(poll_interval_seconds: float) -> float:
    """Return the maximum success age that is still UP (2 × poll interval)."""
    return HEALTHY_WINDOW_POLL_MULTIPLIER * max(float(poll_interval_seconds), 1.0)


def hard_age_cutoff_seconds(poll_interval_seconds: float) -> float:
    """Return the age after which a non-availability failure is DOWN.

    The cutoff is 15 minutes, or 3 × the poll interval when that is longer.
    """
    interval = max(float(poll_interval_seconds), 1.0)
    return max(float(HARD_AGE_SECONDS), HARD_AGE_POLL_MULTIPLIER * interval)


def is_auth_code(code: str | None) -> bool:
    """True for a hard auth failure (HTTP 401 or 403)."""
    return code in AUTH_CODES


def is_upstream_degraded_code(code: str | None) -> bool:
    """True when a live poller should stay DEGRADED past the hard age cutoff."""
    if not code:
        return False
    if code in {"408", "429"}:
        return True
    if code.isdigit() and 500 <= int(code) <= 599:
        return True
    return code in UPSTREAM_DEGRADED_NAMES


def classify_overall(
    *,
    now: float,
    poll_interval_seconds: float,
    last_success_at: float | None,
    consecutive_failures: int,
    last_error_code: str | None,
    poll_thread_alive: bool,
) -> str:
    """Return ``UP``, ``DEGRADED``, or ``DOWN``. See the module docstring."""
    if not poll_thread_alive:
        return "DOWN"

    healthy = healthy_window_seconds(poll_interval_seconds)
    hard = hard_age_cutoff_seconds(poll_interval_seconds)
    age = None if last_success_at is None else max(0.0, now - last_success_at)
    failed = consecutive_failures > 0

    if failed and is_auth_code(last_error_code):
        return "DOWN"
    if not failed and age is not None and age <= healthy:
        return "UP"
    # Availability failures stay DEGRADED past the hard cutoff while polling.
    if failed and is_upstream_degraded_code(last_error_code):
        return "DEGRADED"
    if age is None or age > hard:
        return "DOWN"
    return "DEGRADED"


def format_compact(seconds: float) -> str:
    """Short age such as ``47s``, ``17m``, or ``3h 12m``."""
    whole = int(max(0.0, seconds))
    if whole < 60:
        return f"{whole}s"
    minutes = whole // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        if minutes:
            return f"{hours}h {minutes}m"
        return f"{hours}h"
    days, hours = divmod(hours, 24)
    if hours:
        return f"{days}d {hours}h"
    return f"{days}d"


def format_ago(seconds: float) -> str:
    return f"{format_compact(seconds)} ago"


def format_in(seconds: float) -> str:
    return f"~{format_compact(seconds)}"


def overall_phrase(
    state: str, age_seconds: float | None, *, poll_thread_alive: bool
) -> str:
    """Human overall line. UP is just ``UP``; failures include an age phrase."""
    if state == "UP":
        return "UP"
    if not poll_thread_alive:
        return f"{state} — poll thread stopped"
    if age_seconds is None:
        return f"{state} — no success yet"
    return f"{state} — no success in {format_compact(age_seconds)}"


def _pacific() -> ZoneInfo | None:
    try:
        return ZoneInfo(_PT)
    except ZoneInfoNotFoundError:
        return None


def format_timestamp_pt(timestamp: float | None) -> str | None:
    """Absolute time in Pacific, labeled PT (``Oct 27, 2026 10:32:14 PT``)."""
    if timestamp is None:
        return None
    zone = _pacific()
    if zone is None:
        moment = datetime.fromtimestamp(timestamp)
        suffix = "UTC"
    else:
        moment = datetime.fromtimestamp(timestamp, tz=zone)
        suffix = "PT"
    clock = moment.strftime("%H:%M:%S")
    return f"{moment.strftime('%b')} {moment.day}, {moment.year} {clock} {suffix}"


def format_timestamp_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    zone = _pacific()
    if zone is None:
        moment = datetime.fromtimestamp(timestamp)
    else:
        moment = datetime.fromtimestamp(timestamp, tz=zone)
    return moment.isoformat(timespec="seconds")


def redact_text(text: str) -> str:
    """Remove tokens, passwords, auth headers, emails, and coordinates."""
    redacted = _SECRET_QUERY.sub(r"\1REDACTED", text)
    redacted = _BEARER.sub("Bearer REDACTED", redacted)
    redacted = _ENV_SECRET.sub(r"\1\2REDACTED", redacted)
    redacted = _ASSIGNMENT.sub(r"\1\2REDACTED", redacted)
    redacted = _EMAIL.sub("REDACTED", redacted)
    return redacted


def one_line(text: str, *, limit: int = MESSAGE_LIMIT) -> str:
    """Redact, collapse whitespace, and truncate a message to one line."""
    collapsed = _WS.sub(" ", redact_text(text)).strip()
    if len(collapsed) <= limit:
        return collapsed
    if limit <= 3:
        return collapsed[:limit]
    return collapsed[: limit - 3] + "..."


def docker3_access_note(listen_port: int) -> str:
    """Operator access note. The exporter is not publicly listened for humans."""
    return (
        f"Reachable on docker3 as http://127.0.0.1:{int(listen_port)}/status "
        "via lab SSH hop (no public listen)."
    )


def hint_for(*, overall: str, error_code: str | None, cloud_temps_omitted: bool) -> str:
    """Operator hint. Wording follows the signed-off hint catalogue."""
    if overall == "UP":
        text = (
            "No action needed. Zero or absent metrics in daylight with UP "
            "means upstream empty, not scrape down."
        )
    elif is_auth_code(error_code):
        text = "401/403: check API token/cred mount; do not paste into chat."
    elif error_code == "429":
        text = "429: rate limit; backoff is intentional."
    elif error_code in {"SSLError", "gaierror"}:
        text = "TLS/DNS: network path."
    elif error_code == "timeout" or error_code in UPSTREAM_DEGRADED_NAMES:
        text = "timeout: check docker3 outbound / upstream API."
    elif (
        error_code is not None
        and error_code.isdigit()
        and 500 <= int(error_code) <= 599
    ):
        text = "Upstream error: check docker3 outbound / upstream API."
    else:
        text = "Backoff is intentional while the process is still polling."
    if cloud_temps_omitted:
        text = f"{text} Tigo temps: omitted in cloud mode (not an error)."
    if overall != "UP":
        text = f"{text} Don't restart-loop."
    return text


def process_phrase(*, poll_thread_alive: bool, overall: str) -> str:
    if not poll_thread_alive:
        return "Poll thread stopped"
    if overall == "UP":
        return "Healthy"
    return "Running · poll thread alive"


STATE_RULE_SUMMARY = (
    "UP: last success within 2× the poll interval and no auth failure. "
    "DEGRADED: upstream 5xx, timeout, connection, or 429 while the poll "
    "thread is still polling (stays DEGRADED past the hard age cutoff). "
    "DOWN: auth failure (401/403), poll thread stopped, or no success past "
    "the hard age cutoff (15 minutes, or 3× the poll interval when longer) "
    "for any other failure."
)
