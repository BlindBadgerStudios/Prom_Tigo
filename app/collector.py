from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time as dt_time, timedelta
from typing import Any

import requests
import requests.exceptions

from pytigo import TigoPage

_TRANSIENT_ERRORS = (
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
)

from .config import AppConfig
from .metrics import Metrics, clear_labeled_metrics

logger = logging.getLogger(__name__)

POWER_PARAMS = {"Pin"}
VOLTAGE_PARAMS = {"Vin"}
CURRENT_PARAMS = {"Iin"}
SIGNAL_PARAMS = {"RSSI"}
TEMPERATURE_PARAMS = {"Temp", "Tmod", "Tcell", "Tamb"}


@dataclass(slots=True)
class PanelRecord:
    system_id: int
    panel_id: int
    panel_label: str
    panel_serial: str
    panel_type: str
    inverter_id: int | None
    inverter_label: str
    mppt_id: int | None
    mppt_label: str
    string_id: int | None
    string_label: str
    source_id: int | None
    object_id: int | None
    datasource: str
    max_power: float | None = None

    def labels(self) -> dict[str, str]:
        return {
            "system_id": str(self.system_id),
            "panel_id": str(self.panel_id),
            "panel_label": self.panel_label,
            "panel_serial": self.panel_serial,
            "panel_type": self.panel_type,
            "inverter_id": str(self.inverter_id or ""),
            "inverter_label": self.inverter_label,
            "mppt_id": str(self.mppt_id or ""),
            "mppt_label": self.mppt_label,
            "string_id": str(self.string_id or ""),
            "string_label": self.string_label,
            "source_id": str(self.source_id or ""),
            "object_id": str(self.object_id or ""),
            "datasource": self.datasource,
        }


def _ts(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TigoCollector:
    def __init__(self, client: Any, config: AppConfig, metrics: Metrics) -> None:
        self.client = client
        self.config = config
        self.metrics = metrics
        self._system_id = config.system_id
        self._topology_poll_counter = 0
        self._panels_by_object_id: dict[int, PanelRecord] = {}
        self._latest_power_by_object_id: dict[int, float] = {}
        self._last_panel_telemetry_by_object_id: dict[int, datetime] = {}
        self._panel_last_seen_hint_by_object_id: dict[int, datetime] = {}
        self._last_local_device_date: date | None = None

    def login(self) -> None:
        auth = self.client.login()
        raw = getattr(auth, 'raw', None) or {}
        self._last_local_device_date = self._parse_local_device_date(raw.get('sDate'))

    def resolve_system_id(self) -> int:
        if self._system_id is not None:
            return self._system_id
        page = self.client.list_systems()
        if not page.items:
            raise RuntimeError("No Tigo systems available to the configured account")
        self._system_id = int(page.items[0].system_id)
        return self._system_id

    def collect_once(self) -> None:
        system_id = self.resolve_system_id()
        clear_labeled_metrics(self.metrics)
        system = self.client.get_system(system_id)
        summary = self.client.get_summary(system_id)
        sources = self.client.get_sources(system_id)
        alerts = self._safe_get_alerts(system_id)
        self._maybe_refresh_topology(system_id)

        self._record_system_metrics(system, summary, alerts)
        self._record_source_metrics(system_id, sources)
        self._record_panel_topology()
        self._record_panel_status_defaults(system_id)
        self._record_panel_telemetry(system_id, summary)
        self._record_inverter_and_string_rollups()

    def _safe_get_alerts(self, system_id: int) -> TigoPage:
        try:
            return self.client.get_alerts(system_id, limit=self.config.alert_fetch_limit)
        except Exception:
            logger.warning("Could not fetch alerts for system %s", system_id, exc_info=True)
            return TigoPage(items=[])

    def _maybe_refresh_topology(self, system_id: int) -> None:
        self._topology_poll_counter += 1
        if self._panels_by_object_id and self._topology_poll_counter % max(self.config.topology_refresh_interval_polls, 1) != 1:
            return
        layout = self.client.get_layout(system_id)
        objects = self.client.get_objects(system_id)
        objects_by_id = {int(obj.object_id): obj for obj in objects}
        panel_records: dict[int, PanelRecord] = {}
        for inverter in layout.inverters:
            for mppt in inverter.mppts:
                for string in mppt.strings:
                    for panel in string.panels:
                        if panel.object_id is None:
                            continue
                        obj = objects_by_id.get(int(panel.object_id))
                        datasource = obj.datasource if obj and obj.datasource else ""
                        obj_ui = getattr(obj, 'ui', None) if obj else None
                        max_power = obj_ui.max_power if obj_ui else None
                        panel_records[int(panel.object_id)] = PanelRecord(
                            system_id=system_id,
                            panel_id=int(panel.panel_id),
                            panel_label=panel.label or f"panel_{panel.panel_id}",
                            panel_serial=panel.serial or "",
                            panel_type=panel.panel_type or "",
                            inverter_id=getattr(inverter, 'inverter_id', None),
                            inverter_label=inverter.label or "",
                            mppt_id=getattr(mppt, 'mppt_id', None),
                            mppt_label=mppt.label or "",
                            string_id=getattr(string, 'string_id', None),
                            string_label=string.label or "",
                            source_id=panel.source_id,
                            object_id=panel.object_id,
                            datasource=datasource,
                            max_power=max_power,
                        )
        self._panels_by_object_id = panel_records

    def _record_system_metrics(self, system: Any, summary: Any, alerts: TigoPage) -> None:
        system_id = str(system.system_id)
        system_name = system.name or f"system_{system.system_id}"
        status = system.status or ""
        self.metrics.system_info.labels(
            system_id=system_id,
            system_name=system_name,
            timezone=system.timezone or "",
            status=status,
        ).set(1)
        alert_count = getattr(system, 'recent_alerts_count', None)
        if alert_count is None:
            alert_count = alerts.total if alerts.total is not None else len(alerts.items)
        self.metrics.system_recent_alerts_count.labels(system_id=system_id, system_name=system_name).set(float(alert_count))
        self.metrics.system_has_monitored_modules.labels(system_id=system_id, system_name=system_name).set(
            1 if getattr(system, 'has_monitored_modules', False) else 0
        )
        for gauge, value in (
            (self.metrics.system_last_power_dc_watts, getattr(summary, 'last_power_dc', None)),
            (self.metrics.system_daily_energy_dc_watt_hours, getattr(summary, 'daily_energy_dc', None)),
            (self.metrics.system_ytd_energy_dc_watt_hours, getattr(summary, 'ytd_energy_dc', None)),
            (self.metrics.system_lifetime_energy_dc_watt_hours, getattr(summary, 'lifetime_energy_dc', None)),
            (self.metrics.system_power_rating_dc_watts, getattr(system, 'power_rating', None)),
            (self.metrics.system_power_rating_ac_watts, getattr(system, 'power_rating_ac', None)),
        ):
            numeric = _coerce_float(value)
            if numeric is not None:
                gauge.labels(system_id=system_id, system_name=system_name).set(numeric)
        updated = _ts(getattr(summary, 'updated_on', None))
        if updated is not None:
            self.metrics.system_summary_updated_timestamp_seconds.labels(system_id=system_id, system_name=system_name).set(updated)

    def _record_source_metrics(self, system_id: int, sources: list[Any]) -> None:
        now_ts = datetime.now(tz=UTC).timestamp()
        for source in sources:
            labels = {
                'system_id': str(system_id),
                'source_id': str(source.source_id),
                'source_name': source.name or f"source_{source.source_id}",
                'serial': source.serial or "",
                'timezone': source.timezone or "",
                'sw_version': source.sw_version or "",
            }
            simple = {
                'system_id': str(system_id),
                'source_id': str(source.source_id),
                'source_name': source.name or f"source_{source.source_id}",
            }
            self.metrics.source_info.labels(**labels).set(1)
            if source.gateway_count is not None:
                self.metrics.source_gateway_count.labels(**simple).set(float(source.gateway_count))
            if source.panel_count is not None:
                self.metrics.source_panel_count.labels(**simple).set(float(source.panel_count))
            self.metrics.source_discovery_complete.labels(**simple).set(1 if getattr(source, 'is_discovery_complete', False) else 0)
            checkin_ts = _ts(getattr(source, 'last_checkin', None))
            if checkin_ts is not None:
                self.metrics.source_last_checkin_timestamp_seconds.labels(**simple).set(checkin_ts)
                is_up = 1 if now_ts - checkin_ts <= self.config.source_stale_after_seconds else 0
                self.metrics.source_up.labels(**simple).set(is_up)
            for set_item in getattr(source, 'sets', []):
                set_labels = dict(labels)
                set_labels['set_name'] = set_item.set_name
                if _ts(getattr(set_item, 'last_min', None)) is not None:
                    self.metrics.source_set_last_min_timestamp_seconds.labels(**set_labels).set(_ts(set_item.last_min))
                if _ts(getattr(set_item, 'last_day', None)) is not None:
                    self.metrics.source_set_last_day_timestamp_seconds.labels(**set_labels).set(_ts(set_item.last_day))
                if _ts(getattr(set_item, 'last_raw', None)) is not None:
                    self.metrics.source_set_last_raw_timestamp_seconds.labels(**set_labels).set(_ts(set_item.last_raw))

    def _record_panel_topology(self) -> None:
        for panel in self._panels_by_object_id.values():
            self.metrics.panel_info.labels(**panel.labels()).set(1)
            if panel.max_power is not None:
                self.metrics.panel_power_rating_watts.labels(**panel.labels()).set(panel.max_power)

    def _record_panel_status_defaults(self, system_id: int) -> None:
        for panel in self._panels_by_object_id.values():
            self.metrics.panel_up.labels(
                system_id=str(system_id),
                panel_id=str(panel.panel_id),
                panel_label=panel.panel_label,
            ).set(0)

    def _record_panel_telemetry_defaults(self) -> None:
        for panel in self._panels_by_object_id.values():
            labels = panel.labels()
            self.metrics.panel_power_watts.labels(**labels).set(0)
            self.metrics.panel_voltage_volts.labels(**labels).set(0)
            self.metrics.panel_current_amps.labels(**labels).set(0)
            self.metrics.panel_signal_strength.labels(**labels).set(0)
            self.metrics.panel_temperature_celsius.labels(**labels).set(0)
            for param in self.config.panel_telemetry_params:
                self.metrics.panel_metric_value.labels(**labels, param=param).set(0)

    def _record_panel_telemetry(self, system_id: int, summary: Any) -> None:
        if not self._panels_by_object_id:
            return
        object_ids = sorted(self._panels_by_object_id.keys())
        if not object_ids:
            return
        self._latest_power_by_object_id.clear()
        self._record_panel_telemetry_defaults()

        start, end = self._resolve_panel_window(system_id, object_ids, summary)
        start_str = start.strftime('%Y-%m-%dT%H:%M:%S')
        end_str = end.strftime('%Y-%m-%dT%H:%M:%S')
        current_seen: set[int] = set()
        for param in self.config.panel_telemetry_params:
            try:
                table = self._get_aggregate_with_retry(
                    system_id,
                    start=start_str,
                    end=end_str,
                    param=param,
                    object_ids=object_ids,
                    max_retries=self.config.rate_limit_max_retries,
                    base_delay=self.config.rate_limit_base_delay_seconds,
                )
            except Exception:
                logger.debug("Telemetry param %s unavailable for system %s", param, system_id, exc_info=True)
                continue

            latest_values_for_timestamps = self._latest_values(table.rows, object_ids)
            for object_id, sample in latest_values_for_timestamps.items():
                sample_dt = sample.get('timestamp')
                if sample_dt is not None:
                    previous = self._last_panel_telemetry_by_object_id.get(object_id)
                    if previous is None or sample_dt > previous:
                        self._last_panel_telemetry_by_object_id[object_id] = sample_dt

            if getattr(self.config, 'mode', 'cloud') == 'local':
                current_values = self._latest_row_values(table.rows, object_ids)
            else:
                current_values = latest_values_for_timestamps

            for object_id, sample in current_values.items():
                panel = self._panels_by_object_id.get(object_id)
                if panel is None:
                    continue
                labels = panel.labels()
                value = sample['value']
                if value is None:
                    continue
                current_seen.add(object_id)
                self.metrics.panel_metric_value.labels(**labels, param=param).set(value)
                self._record_param_specific_metric(param, labels, value, object_id)

        now_ts = datetime.now(tz=UTC).timestamp()
        for object_id, panel in self._panels_by_object_id.items():
            hint_dt = self._panel_last_seen_hint_by_object_id.get(object_id)
            last_seen_dt = self._last_panel_telemetry_by_object_id.get(object_id)
            if hint_dt is not None and (last_seen_dt is None or hint_dt > last_seen_dt):
                last_seen_dt = hint_dt
                self._last_panel_telemetry_by_object_id[object_id] = hint_dt
            sample_ts = _ts(last_seen_dt)
            if sample_ts is not None:
                self.metrics.panel_last_telemetry_timestamp_seconds.labels(
                    system_id=str(system_id),
                    panel_id=str(panel.panel_id),
                    panel_label=panel.panel_label,
                ).set(sample_ts)
            if getattr(self.config, 'mode', 'cloud') == 'local':
                is_up = 1 if object_id in current_seen else 0
            else:
                is_up = 1 if sample_ts is not None and now_ts - sample_ts <= self.config.panel_stale_after_seconds else 0
            self.metrics.panel_up.labels(
                system_id=str(system_id),
                panel_id=str(panel.panel_id),
                panel_label=panel.panel_label,
            ).set(is_up)

    def _get_aggregate_with_retry(
        self,
        system_id: int,
        *,
        start: str,
        end: str,
        param: str,
        object_ids: list[int],
        max_retries: int = 3,
        base_delay: float = 5.0,
    ):
        for attempt in range(max_retries):
            try:
                return self.client.get_aggregate(
                    system_id,
                    start=start,
                    end=end,
                    level='min',
                    param=param,
                    object_ids=object_ids,
                    header='id',
                )
            except requests.exceptions.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 429 and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.debug("Rate limited on param %s, retrying in %.1fs (attempt %d/%d)", param, delay, attempt + 1, max_retries)
                    time.sleep(delay)
                else:
                    raise
            except _TRANSIENT_ERRORS as exc:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.debug("Transient error on param %s (%s), retrying in %.1fs (attempt %d/%d)", param, exc, delay, attempt + 1, max_retries)
                    time.sleep(delay)
                else:
                    raise
        raise RuntimeError("unreachable")

    def _resolve_panel_window(self, system_id: int, object_ids: list[int], summary: Any) -> tuple[datetime, datetime]:
        window_minutes = max(self.config.panel_telemetry_window_minutes, 1)
        end = datetime.now(tz=UTC)
        start = end - timedelta(minutes=window_minutes)
        self._panel_last_seen_hint_by_object_id = {}

        # Cloud mode uses the recent window only. Local mode can have valid current-ish
        # telemetry in the latest populated device-day sample even when the most recent
        # wall-clock window is empty, and the summary timestamp can drift onto the wrong
        # day. When recent data is empty, scan the device-reported local day for the
        # freshest row that contains any numeric Pin sample, including zeros.
        if getattr(self.config, 'mode', 'cloud') != 'local':
            return start, end

        try:
            pin_recent = self._get_aggregate_with_retry(
                system_id,
                start=start.strftime('%Y-%m-%dT%H:%M:%S'),
                end=end.strftime('%Y-%m-%dT%H:%M:%S'),
                param='Pin',
                object_ids=object_ids,
                max_retries=self.config.rate_limit_max_retries,
                base_delay=self.config.rate_limit_base_delay_seconds,
            )
            self._panel_last_seen_hint_by_object_id = self._latest_timestamps(pin_recent.rows, object_ids)
            latest_recent_ts = self._latest_row_timestamp(pin_recent.rows, object_ids)
            if latest_recent_ts is not None:
                return start, end
        except Exception:
            logger.debug('Recent Pin window lookup failed for system %s', system_id, exc_info=True)

        local_device_date = self._get_local_device_date(summary)
        if local_device_date is None:
            return start, end

        fallback_day_start, fallback_day_end = self._local_date_bounds_to_utc(local_device_date)
        try:
            pin_day = self._get_aggregate_with_retry(
                system_id,
                start=fallback_day_start.strftime('%Y-%m-%dT%H:%M:%S'),
                end=fallback_day_end.strftime('%Y-%m-%dT%H:%M:%S'),
                param='Pin',
                object_ids=object_ids,
                max_retries=self.config.rate_limit_max_retries,
                base_delay=self.config.rate_limit_base_delay_seconds,
            )
            self._panel_last_seen_hint_by_object_id = self._latest_timestamps(pin_day.rows, object_ids)
            latest_day_ts = self._latest_row_timestamp(pin_day.rows, object_ids)
            if latest_day_ts is not None:
                fallback_end = latest_day_ts
                fallback_start = fallback_end - timedelta(minutes=window_minutes)
                return fallback_start, fallback_end
        except Exception:
            logger.debug('Device-day Pin lookup failed for system %s', system_id, exc_info=True)

        summary_updated = getattr(summary, 'updated_on', None)
        if summary_updated is None:
            return start, end
        if summary_updated.tzinfo is None:
            summary_updated = summary_updated.replace(tzinfo=UTC)
        fallback_end = summary_updated
        fallback_start = fallback_end - timedelta(minutes=window_minutes)
        return fallback_start, fallback_end

    def _latest_values(self, rows: list[Any], object_ids: list[int]) -> dict[int, dict[str, Any]]:
        wanted = {str(object_id): object_id for object_id in object_ids}
        latest: dict[int, dict[str, Any]] = {}
        for row in reversed(rows):
            timestamp = getattr(row, 'timestamp', None)
            values = getattr(row, 'values', {}) or {}
            for key, object_id in wanted.items():
                if object_id in latest:
                    continue
                value = _coerce_float(values.get(key))
                if value is None:
                    continue
                latest[object_id] = {'timestamp': timestamp, 'value': value}
            if len(latest) == len(wanted):
                break
        return latest

    def _latest_row_values(self, rows: list[Any], object_ids: list[int]) -> dict[int, dict[str, Any]]:
        latest_ts = self._latest_row_timestamp(rows, object_ids)
        if latest_ts is None:
            return {}
        wanted = {str(object_id): object_id for object_id in object_ids}
        for row in reversed(rows):
            timestamp = getattr(row, 'timestamp', None)
            values = getattr(row, 'values', {}) or {}
            if timestamp != latest_ts:
                continue
            current: dict[int, dict[str, Any]] = {}
            for key, object_id in wanted.items():
                value = _coerce_float(values.get(key))
                if value is None:
                    continue
                current[object_id] = {'timestamp': timestamp, 'value': value}
            return current
        return {}

    def _latest_timestamps(self, rows: list[Any], object_ids: list[int]) -> dict[int, datetime]:
        wanted = {str(object_id): object_id for object_id in object_ids}
        latest: dict[int, datetime] = {}
        for row in reversed(rows):
            timestamp = getattr(row, 'timestamp', None)
            values = getattr(row, 'values', {}) or {}
            if timestamp is None:
                continue
            for key, object_id in wanted.items():
                if object_id in latest:
                    continue
                if _coerce_float(values.get(key)) is None:
                    continue
                latest[object_id] = timestamp
            if len(latest) == len(wanted):
                break
        return latest

    def _latest_row_timestamp(self, rows: list[Any], object_ids: list[int]) -> datetime | None:
        wanted = {str(object_id) for object_id in object_ids}
        for row in reversed(rows):
            timestamp = getattr(row, 'timestamp', None)
            values = getattr(row, 'values', {}) or {}
            if timestamp is None:
                continue
            if any(_coerce_float(values.get(key)) is not None for key in wanted):
                return timestamp
        return None

    def _parse_local_device_date(self, value: Any) -> date | None:
        if not value:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
        return None

    def _local_date_bounds_to_utc(self, local_device_date: date) -> tuple[datetime, datetime]:
        local_start = datetime.combine(local_device_date, dt_time.min)
        local_end = datetime.combine(local_device_date, dt_time(23, 59, 59))
        offset = timedelta(seconds=getattr(self.config, 'local_tz_offset_seconds', 0))
        return local_start - offset, local_end - offset

    def _get_local_device_date(self, summary: Any) -> date | None:
        if getattr(self.config, 'mode', 'cloud') != 'local':
            return None

        fetcher = getattr(self.client, '_get', None)
        if callable(fetcher):
            try:
                data = fetcher('/cgi-bin/summary_jsconfig')
                device_date = self._parse_local_device_date((data or {}).get('sDate'))
                if device_date is not None:
                    self._last_local_device_date = device_date
                    return device_date
            except Exception:
                logger.debug('Could not refresh local device date from summary_jsconfig', exc_info=True)

        if self._last_local_device_date is not None:
            return self._last_local_device_date

        summary_updated = getattr(summary, 'updated_on', None)
        if summary_updated is None or not hasattr(summary_updated, 'date'):
            return None
        offset = timedelta(seconds=getattr(self.config, 'local_tz_offset_seconds', 0))
        if getattr(summary_updated, 'tzinfo', None) is None:
            summary_updated = summary_updated.replace(tzinfo=UTC)
        local_summary = (summary_updated + offset).date()
        return local_summary

    def _record_param_specific_metric(self, param: str, labels: dict[str, str], value: float, object_id: int) -> None:
        if param in POWER_PARAMS:
            self.metrics.panel_power_watts.labels(**labels).set(value)
            self._latest_power_by_object_id[object_id] = value
        elif param in VOLTAGE_PARAMS:
            self.metrics.panel_voltage_volts.labels(**labels).set(value)
        elif param in CURRENT_PARAMS:
            self.metrics.panel_current_amps.labels(**labels).set(value)
        elif param in SIGNAL_PARAMS:
            self.metrics.panel_signal_strength.labels(**labels).set(value)
        elif param in TEMPERATURE_PARAMS:
            self.metrics.panel_temperature_celsius.labels(**labels).set(value)

    def _record_inverter_and_string_rollups(self) -> None:
        inverter_power: dict[tuple[str, str, str], float] = {}
        string_power: dict[tuple[str, str, str, str, str], float] = {}
        for object_id, panel in self._panels_by_object_id.items():
            power = self._latest_power_by_object_id.get(object_id)
            if power is None:
                continue
            inv_key = (str(panel.system_id), str(panel.inverter_id or ""), panel.inverter_label)
            str_key = (str(panel.system_id), str(panel.inverter_id or ""), panel.inverter_label, str(panel.string_id or ""), panel.string_label)
            inverter_power[inv_key] = inverter_power.get(inv_key, 0.0) + power
            string_power[str_key] = string_power.get(str_key, 0.0) + power
        for (system_id, inverter_id, inverter_label), total in inverter_power.items():
            self.metrics.inverter_power_watts.labels(
                system_id=system_id,
                inverter_id=inverter_id,
                inverter_label=inverter_label,
            ).set(total)
        for (system_id, inverter_id, inverter_label, string_id, string_label), total in string_power.items():
            self.metrics.string_power_watts.labels(
                system_id=system_id,
                inverter_id=inverter_id,
                inverter_label=inverter_label,
                string_id=string_id,
                string_label=string_label,
            ).set(total)
