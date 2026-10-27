"""UP / DEGRADED / DOWN rule, redaction, and time phrases."""

from __future__ import annotations

from datetime import UTC, datetime

from app.status_health import (
    classify_overall,
    format_ago,
    format_timestamp_iso,
    format_timestamp_pt,
    hard_age_cutoff_seconds,
    healthy_window_seconds,
    one_line,
    overall_phrase,
    redact_text,
)

NOW = 1_700_000_000.0
INTERVAL = 60


def classify(**overrides: object) -> str:
    params = {
        "now": NOW,
        "poll_interval_seconds": INTERVAL,
        "last_success_at": NOW - 30,
        "consecutive_failures": 0,
        "last_error_code": None,
        "poll_thread_alive": True,
    }
    params.update(overrides)
    return classify_overall(**params)  # type: ignore[arg-type]


def test_windows_are_two_intervals_and_fifteen_minutes_or_three():
    assert healthy_window_seconds(60) == 120
    assert hard_age_cutoff_seconds(60) == 15 * 60
    assert hard_age_cutoff_seconds(600) == 1800
    # A tiny interval still uses the 15 minute floor.
    assert hard_age_cutoff_seconds(1) == 15 * 60


def test_up_when_the_last_poll_succeeded_inside_the_healthy_window():
    assert classify() == "UP"
    assert classify(last_success_at=NOW - 120) == "UP"
    assert overall_phrase("UP", 47, poll_thread_alive=True) == "UP"


def test_recent_failure_is_not_up_even_if_the_previous_success_is_fresh():
    assert classify(consecutive_failures=1, last_error_code="500") == "DEGRADED"


def test_auth_failure_is_down():
    assert (
        classify(
            last_success_at=NOW - 17 * 60,
            consecutive_failures=4,
            last_error_code="401",
        )
        == "DOWN"
    )
    assert classify(consecutive_failures=1, last_error_code="403") == "DOWN"
    assert (
        overall_phrase("DOWN", 17 * 60, poll_thread_alive=True)
        == "DOWN — no success in 17m"
    )


def test_upstream_outage_stays_degraded_past_the_hard_age_cutoff():
    for code in ("503", "500", "timeout", "ConnectionError", "429", "SSLError"):
        assert (
            classify(
                last_success_at=NOW - 39 * 60,
                consecutive_failures=8,
                last_error_code=code,
            )
            == "DEGRADED"
        )
    assert (
        overall_phrase("DEGRADED", 39 * 60, poll_thread_alive=True)
        == "DEGRADED — no success in 39m"
    )


def test_other_failures_use_the_hard_age_cutoff():
    assert (
        classify(
            last_success_at=NOW - 5 * 60,
            consecutive_failures=1,
            last_error_code="400",
        )
        == "DEGRADED"
    )
    assert (
        classify(
            last_success_at=NOW - 17 * 60,
            consecutive_failures=2,
            last_error_code="400",
        )
        == "DOWN"
    )
    assert (
        classify(
            last_success_at=NOW - 17 * 60,
            consecutive_failures=1,
            last_error_code="ValueError",
        )
        == "DOWN"
    )


def test_no_success_yet_is_down_unless_the_upstream_is_degraded():
    assert classify(last_success_at=None) == "DOWN"
    assert (
        overall_phrase("DOWN", None, poll_thread_alive=True) == "DOWN — no success yet"
    )
    assert (
        classify(last_success_at=None, consecutive_failures=1, last_error_code="503")
        == "DEGRADED"
    )
    assert (
        classify(last_success_at=None, consecutive_failures=1, last_error_code="401")
        == "DOWN"
    )


def test_stale_success_without_a_new_error_degrades_then_goes_down():
    assert classify(last_success_at=NOW - 121) == "DEGRADED"
    assert classify(last_success_at=NOW - 15 * 60) == "DEGRADED"
    assert classify(last_success_at=NOW - 15 * 60 - 1) == "DOWN"


def test_slow_poll_interval_uses_three_intervals_as_the_hard_cutoff():
    assert classify(poll_interval_seconds=600, last_success_at=NOW - 1000) == "UP"
    assert classify(poll_interval_seconds=600, last_success_at=NOW - 1700) == "DEGRADED"
    assert classify(poll_interval_seconds=600, last_success_at=NOW - 1801) == "DOWN"
    assert (
        classify(
            poll_interval_seconds=600,
            last_success_at=NOW - 2000,
            consecutive_failures=3,
            last_error_code="503",
        )
        == "DEGRADED"
    )


def test_stopped_poll_thread_is_down():
    assert classify(poll_thread_alive=False) == "DOWN"
    assert (
        overall_phrase("DOWN", 10, poll_thread_alive=False)
        == "DOWN — poll thread stopped"
    )


def test_relative_and_pacific_timestamps():
    assert format_ago(47) == "47s ago"
    assert format_ago(17 * 60) == "17m ago"
    moment = datetime(2026, 10, 27, 17, 32, 14, tzinfo=UTC).timestamp()
    assert format_timestamp_pt(moment) == "Oct 27, 2026 10:32:14 PT"
    assert format_timestamp_iso(moment) == "2026-10-27T10:32:14-07:00"


def test_redaction_strips_secrets_coordinates_and_long_bodies():
    raw = (
        "401 Client Error for url: https://api.example.test/login?token=supersecret"
        "&lat=45.5123&lon=-122.6789 user@example.com "
        "Authorization: Bearer aaa.bbb.ccc TIGO_PASSWORD=hunter2 " + ("panel " * 80)
    )
    cleaned = one_line(raw)
    assert "supersecret" not in cleaned
    assert "45.5123" not in cleaned
    assert "-122.6789" not in cleaned
    assert "user@example.com" not in cleaned
    assert "hunter2" not in cleaned
    assert "aaa.bbb.ccc" not in cleaned
    assert "token=REDACTED" in cleaned
    assert len(cleaned) <= 200
    assert redact_text("password: hunter2") == "password: REDACTED"
