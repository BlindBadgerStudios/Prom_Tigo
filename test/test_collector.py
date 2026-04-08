from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pytigo import TigoPage

from app.collector import TigoCollector
from app.config import AppConfig
from app.metrics import build_metrics


@dataclass
class FakeRow:
    timestamp: datetime
    values: dict[str, float | None]


@dataclass
class FakeTable:
    rows: list[FakeRow]


class FakeClient:
    def login(self):
        return None

    def list_systems(self):
        return TigoPage(items=[type('System', (), {'system_id': 123, 'name': 'Array One'})()])

    def get_system(self, system_id):
        return type('System', (), {
            'system_id': system_id,
            'name': 'Array One',
            'timezone': 'America/Chicago',
            'status': 'active',
            'recent_alerts_count': 2,
            'has_monitored_modules': True,
            'power_rating': 8500.0,
            'power_rating_ac': 7600.0,
        })()

    def get_summary(self, system_id):
        return type('Summary', (), {
            'last_power_dc': 5100.0,
            'daily_energy_dc': 32000.0,
            'ytd_energy_dc': 420000.0,
            'lifetime_energy_dc': 5000000.0,
            'updated_on': datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        })()

    def get_sources(self, system_id):
        source_set = type('Set', (), {
            'set_name': 'panels_avg',
            'last_min': datetime.now(tz=UTC) - timedelta(minutes=1),
            'last_day': datetime.now(tz=UTC) - timedelta(hours=2),
            'last_raw': datetime.now(tz=UTC) - timedelta(minutes=1),
        })()
        return [type('Source', (), {
            'source_id': 200,
            'name': 'CCA',
            'serial': 'SRC-1',
            'timezone': 'America/Chicago',
            'sw_version': '1.2.3',
            'gateway_count': 1,
            'panel_count': 2,
            'is_discovery_complete': True,
            'last_checkin': datetime.now(tz=UTC) - timedelta(minutes=1),
            'sets': [source_set],
        })()]

    def get_alerts(self, system_id, limit=200):
        return TigoPage(items=[], total=2)

    def get_layout(self, system_id):
        panel1 = type('Panel', (), {'panel_id': 1, 'label': 'A1', 'serial': 'OPT-A1', 'panel_type': 'TS4', 'source_id': 200, 'object_id': 1001})()
        panel2 = type('Panel', (), {'panel_id': 2, 'label': 'A2', 'serial': 'OPT-A2', 'panel_type': 'TS4', 'source_id': 200, 'object_id': 1002})()
        string = type('String', (), {'string_id': 300, 'label': 'String A', 'panels': [panel1, panel2]})()
        mppt = type('Mppt', (), {'mppt_id': 400, 'label': 'MPPT 1', 'strings': [string]})()
        inverter = type('Inverter', (), {'inverter_id': 500, 'label': 'INV-1', 'mppts': [mppt]})()
        return type('Layout', (), {'inverters': [inverter]})()

    def get_objects(self, system_id):
        ui = type('UI', (), {'max_power': 375.0})()
        return [
            type('Obj', (), {'object_id': 1001, 'datasource': 'SOURCE.panels.A1', 'ui': ui})(),
            type('Obj', (), {'object_id': 1002, 'datasource': 'SOURCE.panels.A2', 'ui': ui})(),
        ]

    def get_aggregate(self, system_id, *, start, end, level, param, object_ids, header):
        now = datetime.now(tz=UTC)
        data = {
            'Pin': {'1001': 320.5, '1002': 315.2},
            'Vin': {'1001': 39.8, '1002': 39.5},
            'Iin': {'1001': 8.05, '1002': 7.98},
            'RSSI': {'1001': -71.0, '1002': -70.0},
            'Temp': {'1001': 45.0, '1002': 44.0},
        }
        values = data.get(param, {})
        return FakeTable(rows=[FakeRow(timestamp=now, values=values)])


def test_collect_once_populates_metrics():
    metrics = build_metrics()
    config = AppConfig(
        username='user',
        password='pass',
        system_id=123,
        panel_telemetry_params=['Pin', 'Vin', 'Iin', 'RSSI', 'Temp'],
        panel_stale_after_seconds=900,
    )
    collector = TigoCollector(client=FakeClient(), config=config, metrics=metrics)

    collector.collect_once()

    exported = metrics.registry.get_sample_value(
        'tigo_panel_power_watts',
        labels={
            'system_id': '123',
            'panel_id': '1',
            'panel_label': 'A1',
            'panel_serial': 'OPT-A1',
            'panel_type': 'TS4',
            'inverter_id': '500',
            'inverter_label': 'INV-1',
            'mppt_id': '400',
            'mppt_label': 'MPPT 1',
            'string_id': '300',
            'string_label': 'String A',
            'source_id': '200',
            'object_id': '1001',
            'datasource': 'SOURCE.panels.A1',
        },
    )
    assert exported == 320.5
    assert metrics.registry.get_sample_value(
        'tigo_source_up',
        labels={'system_id': '123', 'source_id': '200', 'source_name': 'CCA'},
    ) == 1.0
    assert metrics.registry.get_sample_value(
        'tigo_panel_up',
        labels={'system_id': '123', 'panel_id': '1', 'panel_label': 'A1'},
    ) == 1.0
    assert metrics.registry.get_sample_value(
        'tigo_panel_temperature_celsius',
        labels={
            'system_id': '123',
            'panel_id': '1',
            'panel_label': 'A1',
            'panel_serial': 'OPT-A1',
            'panel_type': 'TS4',
            'inverter_id': '500',
            'inverter_label': 'INV-1',
            'mppt_id': '400',
            'mppt_label': 'MPPT 1',
            'string_id': '300',
            'string_label': 'String A',
            'source_id': '200',
            'object_id': '1001',
            'datasource': 'SOURCE.panels.A1',
        },
    ) == 45.0

    # System capacity ratings
    assert metrics.registry.get_sample_value(
        'tigo_system_power_rating_dc_watts',
        labels={'system_id': '123', 'system_name': 'Array One'},
    ) == 8500.0
    assert metrics.registry.get_sample_value(
        'tigo_system_power_rating_ac_watts',
        labels={'system_id': '123', 'system_name': 'Array One'},
    ) == 7600.0

    # Per-panel rated max power from topology
    panel_labels = {
        'system_id': '123',
        'panel_id': '1',
        'panel_label': 'A1',
        'panel_serial': 'OPT-A1',
        'panel_type': 'TS4',
        'inverter_id': '500',
        'inverter_label': 'INV-1',
        'mppt_id': '400',
        'mppt_label': 'MPPT 1',
        'string_id': '300',
        'string_label': 'String A',
        'source_id': '200',
        'object_id': '1001',
        'datasource': 'SOURCE.panels.A1',
    }
    assert metrics.registry.get_sample_value('tigo_panel_power_rating_watts', labels=panel_labels) == 375.0

    # Inverter and string power rollups
    assert metrics.registry.get_sample_value(
        'tigo_inverter_power_watts',
        labels={'system_id': '123', 'inverter_id': '500', 'inverter_label': 'INV-1'},
    ) == 320.5 + 315.2
    assert metrics.registry.get_sample_value(
        'tigo_string_power_watts',
        labels={'system_id': '123', 'inverter_id': '500', 'inverter_label': 'INV-1', 'string_id': '300', 'string_label': 'String A'},
    ) == 320.5 + 315.2


class FakeLocalFallbackClient(FakeClient):
    def get_summary(self, system_id):
        return type('Summary', (), {
            'last_power_dc': 17410.0,
            'daily_energy_dc': 32000.0,
            'ytd_energy_dc': None,
            'lifetime_energy_dc': None,
            'updated_on': datetime(2026, 4, 8, 11, 4, tzinfo=UTC),
        })()

    def get_aggregate(self, system_id, *, start, end, level, param, object_ids, header):
        recent_start = '2026-04-08T17:45:00'
        recent_end = '2026-04-08T18:00:00'
        fallback_start = '2026-04-08T10:49:00'
        fallback_end = '2026-04-08T11:04:00'
        if start == recent_start and end == recent_end:
            return FakeTable(rows=[])
        if start == fallback_start and end == fallback_end:
            ts = datetime(2026, 4, 8, 11, 4, tzinfo=UTC)
            data = {
                'Pin': {'1001': 335.0, '1002': 334.0},
                'Vin': {'1001': 3.2, '1002': 3.1},
                'Iin': {'1001': 104.6875, '1002': 107.7419354839},
                'RSSI': {'1001': 150.0, '1002': 149.0},
            }
            return FakeTable(rows=[FakeRow(timestamp=ts, values=data.get(param, {}))])
        return FakeTable(rows=[])


def test_collect_once_local_falls_back_to_latest_populated_window():
    metrics = build_metrics()
    config = AppConfig(
        mode='local',
        system_id=123,
        local_host='192.168.192.114',
        local_username='Tigo',
        local_password='$olar',
        panel_telemetry_params=['Pin', 'Vin', 'Iin', 'RSSI'],
        panel_stale_after_seconds=900,
    )
    collector = TigoCollector(client=FakeLocalFallbackClient(), config=config, metrics=metrics)

    # Freeze collector's notion of now so the recent window is deterministic and empty.
    import app.collector as collector_module
    real_datetime = collector_module.datetime

    class FakeDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 8, 18, 0, 0, tzinfo=tz or UTC)

    collector_module.datetime = FakeDateTime
    try:
        collector.collect_once()
    finally:
        collector_module.datetime = real_datetime

    panel_labels = {
        'system_id': '123',
        'panel_id': '1',
        'panel_label': 'A1',
        'panel_serial': 'OPT-A1',
        'panel_type': 'TS4',
        'inverter_id': '500',
        'inverter_label': 'INV-1',
        'mppt_id': '400',
        'mppt_label': 'MPPT 1',
        'string_id': '300',
        'string_label': 'String A',
        'source_id': '200',
        'object_id': '1001',
        'datasource': 'SOURCE.panels.A1',
    }
    assert metrics.registry.get_sample_value('tigo_panel_power_watts', labels=panel_labels) == 335.0
    assert metrics.registry.get_sample_value('tigo_panel_voltage_volts', labels=panel_labels) == 3.2
    assert metrics.registry.get_sample_value('tigo_panel_current_amps', labels=panel_labels) == 104.6875
    assert metrics.registry.get_sample_value('tigo_panel_signal_strength', labels=panel_labels) == 150.0
    assert metrics.registry.get_sample_value(
        'tigo_panel_up',
        labels={'system_id': '123', 'panel_id': '1', 'panel_label': 'A1'},
    ) == 1.0
