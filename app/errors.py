"""Helpers for classifying API errors (status labels, auth errors, Retry-After)."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import requests

AUTH_STATUS_CODES = frozenset({401, 403})


def http_status(exc: BaseException) -> int | None:
    """Return the HTTP status code carried by a requests HTTPError, if any."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def status_label(exc: BaseException) -> str:
    """Low-cardinality label for an error: the HTTP status code or the exception class name."""
    status = http_status(exc)
    if status is not None:
        return str(status)
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    return type(exc).__name__


def is_auth_error(exc: BaseException) -> bool:
    """True for 401/403 responses, the only errors that warrant a fresh login."""
    return http_status(exc) in AUTH_STATUS_CODES


def retry_after_seconds(exc: BaseException, *, now: datetime | None = None) -> float | None:
    """Parse Retry-After (delta-seconds or HTTP-date) from a 429/503 HTTPError response."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After")
    if value in (None, ""):
        return None
    value = str(value).strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    current = now or datetime.now(tz=UTC)
    return max((when - current).total_seconds(), 0.0)
