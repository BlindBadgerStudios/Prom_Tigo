from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
import requests

import app.collector as collector_module
from app.collector import TigoCollector
from app.config import AppConfig
from app.errors import is_auth_error, retry_after_seconds, status_label
from app.metrics import build_metrics

from test_collector import FakeClient

PANEL1 = {
    'system_id': '123', 'panel_id': '1', 'panel_label': 'A1', 'panel_serial': 'OPT-A1', 'panel_type': 'TS4',
    'inverter_id': '500', 'inverter_label': 'INV-1', 'mppt_id': '400', 'mppt_label': 'MPPT 1',
    'string_id': '300', 'string_label': 'String A', 'source_id': '200', 'object_id': '1001',
    'datasource': 'SOURCE.panels.A1',
}


def http_error(status: int, headers: dict[str, str] | None = None) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    return requests.exceptions.HTTPError(f"HTTP {status}", response=response)


class FailingParamClient(FakeClient):
    """Cloud-like client: temperature names get HTTP 400, Vin fails with a connection error."""

    def __init__(self):
        self.calls: list[str] = []

    def get_aggregate(self, system_id, *, start, end, level, param, object_ids, header):
        self.calls.append(param)
        if param == 'Temp':
            raise http_error(400)
        if param == 'Vin':
            raise requests.exceptions.ConnectionError('boom')
        return super().get_aggregate(system_id, start=start, end=end, level=level, param=param, object_ids=object_ids, header=header)


def _config(params):
    return AppConfig(
        username='user', password='pass', system_id=123,
        panel_telemetry_params=params, panel_stale_after_seconds=900,
        rate_limit_max_retries=1, rate_limit_base_delay_seconds=0.0,
    )


def test_failed_params_are_absent_counted_and_warned_once(caplog):
    metrics = build_metrics()
    collector = TigoCollector(client=FailingParamClient(), config=_config(['Pin', 'Vin', 'Temp']), metrics=metrics)

    with caplog.at_level(logging.WARNING, logger='app.collector'):
        collector.collect_once()

    reg = metrics.registry
    assert reg.get_sample_value('tigo_panel_power_watts', labels=PANEL1) == 320.5
    # Failed params: series absent, not 0.
    assert reg.get_sample_value('tigo_panel_voltage_volts', labels=PANEL1) is None
    assert reg.get_sample_value('tigo_panel_temperature_celsius', labels=PANEL1) is None
    assert reg.get_sample_value('tigo_panel_metric_value', labels={**PANEL1, 'param': 'Vin'}) is None
    assert reg.get_sample_value('tigo_panel_metric_value', labels={**PANEL1, 'param': 'Temp'}) is None
    # Params never requested are absent too (no pre-filled zero).
    assert reg.get_sample_value('tigo_panel_current_amps', labels=PANEL1) is None
    assert reg.get_sample_value('tigo_panel_signal_strength', labels=PANEL1) is None

    assert reg.get_sample_value(
        'tigo_exporter_api_errors_total', labels={'endpoint': 'data/aggregate', 'param': 'Temp', 'status': '400'}
    ) == 1.0
    assert reg.get_sample_value(
        'tigo_exporter_api_errors_total', labels={'endpoint': 'data/aggregate', 'param': 'Vin', 'status': 'ConnectionError'}
    ) == 1.0

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and 'Panel telemetry unavailable' in r.getMessage()]
    assert len(warnings) == 1
    assert 'Temp (400)' in warnings[0].getMessage()
    assert 'Vin (ConnectionError)' in warnings[0].getMessage()


def test_series_from_previous_poll_disappears_when_param_fails_next_poll():
    metrics = build_metrics()
    client = FailingParamClient()
    collector = TigoCollector(client=client, config=_config(['Pin', 'RSSI']), metrics=metrics)
    collector.collect_once()
    assert metrics.registry.get_sample_value('tigo_panel_signal_strength', labels=PANEL1) == -71.0

    original = client.get_aggregate

    def rssi_fails(system_id, **kwargs):
        if kwargs['param'] == 'RSSI':
            raise http_error(500)
        return original(system_id, **kwargs)

    client.get_aggregate = rssi_fails
    collector.collect_once()
    assert metrics.registry.get_sample_value('tigo_panel_signal_strength', labels=PANEL1) is None
    assert metrics.registry.get_sample_value('tigo_panel_power_watts', labels=PANEL1) == 320.5


def test_alert_failures_are_counted():
    metrics = build_metrics()
    client = FakeClient()

    def boom(system_id, limit=200):
        raise http_error(429)

    client.get_alerts = boom
    TigoCollector(client=client, config=_config(['Pin']), metrics=metrics).collect_once()
    assert metrics.registry.get_sample_value(
        'tigo_exporter_api_errors_total', labels={'endpoint': 'alerts/system', 'param': '', 'status': '429'}
    ) == 1.0


def test_aggregate_retry_honors_retry_after(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(collector_module.time, 'sleep', sleeps.append)

    class RateLimitedOnce(FakeClient):
        def __init__(self):
            self.attempts = 0

        def get_aggregate(self, system_id, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise http_error(429, {'Retry-After': '17'})
            return super().get_aggregate(system_id, **kwargs)

    metrics = build_metrics()
    collector = TigoCollector(client=RateLimitedOnce(), config=_config(['Pin']), metrics=metrics)
    table = collector._get_aggregate_with_retry(123, start='a', end='b', param='Pin', object_ids=[1001], max_retries=3, base_delay=1.0)
    assert table.rows
    assert sleeps == [17.0]


def test_aggregate_retry_delay_is_capped(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(collector_module.time, 'sleep', sleeps.append)

    class AlwaysLimited(FakeClient):
        def get_aggregate(self, system_id, **kwargs):
            raise http_error(429, {'Retry-After': '3600'})

    collector = TigoCollector(client=AlwaysLimited(), config=_config(['Pin']), metrics=build_metrics())
    with pytest.raises(requests.exceptions.HTTPError):
        collector._get_aggregate_with_retry(123, start='a', end='b', param='Pin', object_ids=[1001], max_retries=2, base_delay=1.0)
    assert sleeps == [collector_module.MAX_RETRY_DELAY_SECONDS]


def test_error_helpers():
    assert status_label(http_error(503)) == '503'
    assert status_label(requests.exceptions.ReadTimeout()) == 'timeout'
    assert status_label(ValueError('x')) == 'ValueError'
    assert is_auth_error(http_error(401))
    assert is_auth_error(http_error(403))
    assert not is_auth_error(http_error(429))
    assert not is_auth_error(RuntimeError('x'))
    assert retry_after_seconds(http_error(429, {'Retry-After': '5'})) == 5.0
    assert retry_after_seconds(http_error(429)) is None
    assert retry_after_seconds(http_error(429, {'Retry-After': 'garbage'})) is None
    now = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
    assert retry_after_seconds(http_error(429, {'Retry-After': 'Tue, 13 Oct 2026 12:00:30 GMT'}), now=now) == 30.0
    assert retry_after_seconds(http_error(429, {'Retry-After': 'Tue, 13 Oct 2026 11:00:00 GMT'}), now=now) == 0.0
    assert retry_after_seconds(RuntimeError('x')) is None


def test_exporter_up_starts_at_zero():
    assert build_metrics().registry.get_sample_value('tigo_exporter_up') == 0.0
