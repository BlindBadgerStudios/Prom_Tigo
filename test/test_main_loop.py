from __future__ import annotations

import random

import pytest
import requests

from app.main import MAX_BACKOFF_SECONDS, LoopState, _failure_delay, _run_cycle
from app.metrics import build_metrics


def http_error(status: int, headers: dict[str, str] | None = None) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    return requests.exceptions.HTTPError(f"HTTP {status}", response=response)


class ScriptedCollector:
    """collect_once() raises the scripted exceptions in order, then succeeds."""

    def __init__(self, failures=()):
        self.failures = list(failures)
        self.logins = 0
        self.polls = 0

    def login(self):
        self.logins += 1

    def collect_once(self):
        self.polls += 1
        if self.failures:
            raise self.failures.pop(0)


class MaxRng(random.Random):
    def uniform(self, a, b):
        return b


def run(collector, n, metrics=None, state=None, interval=60):
    metrics = metrics or build_metrics()
    state = state or LoopState()
    delays = [_run_cycle(collector, interval, metrics, state, rng=MaxRng()) for _ in range(n)]
    return delays, metrics, state


@pytest.mark.parametrize('exc', [http_error(429), http_error(400), http_error(500), requests.exceptions.ConnectionError(), ValueError('parse')])
def test_non_auth_failures_do_not_trigger_relogin(exc):
    collector = ScriptedCollector([exc, exc])
    run(collector, 3)
    assert collector.logins == 1
    assert collector.polls == 3


@pytest.mark.parametrize('status', [401, 403])
def test_auth_failures_trigger_relogin(status):
    collector = ScriptedCollector([http_error(status)])
    run(collector, 2)
    assert collector.logins == 2


def test_backoff_grows_and_resets_after_success():
    collector = ScriptedCollector([http_error(500)] * 4)
    delays, metrics, state = run(collector, 5)
    assert delays == [60.0, 120.0, 240.0, 480.0, 60.0]
    assert state.consecutive_failures == 0
    assert metrics.registry.get_sample_value('tigo_exporter_up') == 1.0
    assert metrics.registry.get_sample_value('tigo_exporter_errors_total') == 4.0
    assert metrics.registry.get_sample_value(
        'tigo_exporter_api_errors_total', labels={'endpoint': 'poll', 'param': '', 'status': '500'}
    ) == 4.0


def test_backoff_is_capped_and_jittered():
    assert _failure_delay(60, 20, rng=MaxRng()) == MAX_BACKOFF_SECONDS
    rng = random.Random(1)
    samples = [_failure_delay(60, 3, rng=rng) for _ in range(50)]
    assert all(120.0 <= d <= 240.0 for d in samples)
    assert len(set(samples)) > 1


def test_retry_after_on_429_is_a_floor():
    collector = ScriptedCollector([http_error(429, {'Retry-After': '1200'})])
    delays, _, _ = run(collector, 1)
    assert delays == [1200.0]


def test_retry_after_ignored_for_non_429():
    collector = ScriptedCollector([http_error(503, {'Retry-After': '1200'})])
    delays, _, _ = run(collector, 1)
    assert delays == [60.0]


def test_failed_poll_sets_up_to_zero():
    metrics = build_metrics()
    assert metrics.registry.get_sample_value('tigo_exporter_up') == 0.0
    run(ScriptedCollector(), 1, metrics=metrics)
    assert metrics.registry.get_sample_value('tigo_exporter_up') == 1.0
    run(ScriptedCollector([http_error(502)]), 1, metrics=metrics)
    assert metrics.registry.get_sample_value('tigo_exporter_up') == 0.0
