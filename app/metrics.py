from __future__ import annotations

from dataclasses import dataclass
from prometheus_client import CollectorRegistry, Counter, Gauge


@dataclass(slots=True)
class Metrics:
    registry: CollectorRegistry
    exporter_up: Gauge
    exporter_last_success_timestamp: Gauge
    exporter_poll_duration: Gauge
    exporter_errors_total: Counter
    system_info: Gauge
    system_recent_alerts_count: Gauge
    system_has_monitored_modules: Gauge
    system_last_power_dc_watts: Gauge
    system_daily_energy_dc_watt_hours: Gauge
    system_ytd_energy_dc_watt_hours: Gauge
    system_lifetime_energy_dc_watt_hours: Gauge
    system_summary_updated_timestamp_seconds: Gauge
    source_info: Gauge
    source_gateway_count: Gauge
    source_panel_count: Gauge
    source_discovery_complete: Gauge
    source_last_checkin_timestamp_seconds: Gauge
    source_up: Gauge
    source_set_last_min_timestamp_seconds: Gauge
    source_set_last_day_timestamp_seconds: Gauge
    source_set_last_raw_timestamp_seconds: Gauge
    panel_info: Gauge
    panel_up: Gauge
    panel_last_telemetry_timestamp_seconds: Gauge
    panel_power_watts: Gauge
    panel_voltage_volts: Gauge
    panel_current_amps: Gauge
    panel_signal_strength: Gauge
    panel_temperature_celsius: Gauge
    panel_metric_value: Gauge
    panel_power_rating_watts: Gauge
    inverter_power_watts: Gauge
    string_power_watts: Gauge
    system_power_rating_dc_watts: Gauge
    system_power_rating_ac_watts: Gauge


def build_metrics(registry: CollectorRegistry | None = None) -> Metrics:
    registry = registry or CollectorRegistry()
    system_labels = ["system_id", "system_name", "timezone", "status"]
    source_labels = ["system_id", "source_id", "source_name", "serial", "timezone", "sw_version"]
    source_set_labels = source_labels + ["set_name"]
    panel_labels = [
        "system_id", "panel_id", "panel_label", "panel_serial", "panel_type",
        "inverter_id", "inverter_label", "mppt_id", "mppt_label", "string_id", "string_label",
        "source_id", "object_id", "datasource",
    ]
    return Metrics(
        registry=registry,
        exporter_up=Gauge("tigo_exporter_up", "1 if the last poll succeeded", registry=registry),
        exporter_last_success_timestamp=Gauge(
            "tigo_exporter_last_success_timestamp_seconds",
            "Unix timestamp of the last successful poll",
            registry=registry,
        ),
        exporter_poll_duration=Gauge(
            "tigo_exporter_poll_duration_seconds",
            "Duration of the last poll in seconds",
            registry=registry,
        ),
        exporter_errors_total=Counter(
            "tigo_exporter_errors_total",
            "Total exporter polling errors",
            registry=registry,
        ),
        system_info=Gauge("tigo_system_info", "Static system info metric (value always 1)", system_labels, registry=registry),
        system_recent_alerts_count=Gauge("tigo_system_recent_alerts_count", "Recent alerts count reported by Tigo", ["system_id", "system_name"], registry=registry),
        system_has_monitored_modules=Gauge("tigo_system_has_monitored_modules", "1 if the system reports monitored modules", ["system_id", "system_name"], registry=registry),
        system_last_power_dc_watts=Gauge("tigo_system_last_power_dc_watts", "Last reported DC power", ["system_id", "system_name"], registry=registry),
        system_daily_energy_dc_watt_hours=Gauge("tigo_system_daily_energy_dc_watt_hours", "Daily DC energy", ["system_id", "system_name"], registry=registry),
        system_ytd_energy_dc_watt_hours=Gauge("tigo_system_ytd_energy_dc_watt_hours", "Year-to-date DC energy", ["system_id", "system_name"], registry=registry),
        system_lifetime_energy_dc_watt_hours=Gauge("tigo_system_lifetime_energy_dc_watt_hours", "Lifetime DC energy", ["system_id", "system_name"], registry=registry),
        system_summary_updated_timestamp_seconds=Gauge("tigo_system_summary_updated_timestamp_seconds", "Timestamp of the summary payload", ["system_id", "system_name"], registry=registry),
        source_info=Gauge("tigo_source_info", "Static source info metric (value always 1)", source_labels, registry=registry),
        source_gateway_count=Gauge("tigo_source_gateway_count", "Number of gateways reported for this source", ["system_id", "source_id", "source_name"], registry=registry),
        source_panel_count=Gauge("tigo_source_panel_count", "Number of panels reported for this source", ["system_id", "source_id", "source_name"], registry=registry),
        source_discovery_complete=Gauge("tigo_source_discovery_complete", "1 if source discovery is complete", ["system_id", "source_id", "source_name"], registry=registry),
        source_last_checkin_timestamp_seconds=Gauge("tigo_source_last_checkin_timestamp_seconds", "Timestamp of the last source checkin", ["system_id", "source_id", "source_name"], registry=registry),
        source_up=Gauge("tigo_source_up", "1 if the source checked in recently", ["system_id", "source_id", "source_name"], registry=registry),
        source_set_last_min_timestamp_seconds=Gauge("tigo_source_set_last_min_timestamp_seconds", "Timestamp of the set's latest minute data", source_set_labels, registry=registry),
        source_set_last_day_timestamp_seconds=Gauge("tigo_source_set_last_day_timestamp_seconds", "Timestamp of the set's latest daily data", source_set_labels, registry=registry),
        source_set_last_raw_timestamp_seconds=Gauge("tigo_source_set_last_raw_timestamp_seconds", "Timestamp of the set's latest raw data", source_set_labels, registry=registry),
        panel_info=Gauge("tigo_panel_info", "Static panel topology info metric (value always 1)", panel_labels, registry=registry),
        panel_up=Gauge("tigo_panel_up", "1 if recent telemetry was found for the panel", ["system_id", "panel_id", "panel_label"], registry=registry),
        panel_last_telemetry_timestamp_seconds=Gauge("tigo_panel_last_telemetry_timestamp_seconds", "Timestamp of the latest telemetry sample for the panel", ["system_id", "panel_id", "panel_label"], registry=registry),
        panel_power_watts=Gauge("tigo_panel_power_watts", "Latest panel power telemetry", panel_labels, registry=registry),
        panel_voltage_volts=Gauge("tigo_panel_voltage_volts", "Latest panel voltage telemetry", panel_labels, registry=registry),
        panel_current_amps=Gauge("tigo_panel_current_amps", "Latest panel current telemetry", panel_labels, registry=registry),
        panel_signal_strength=Gauge("tigo_panel_signal_strength", "Latest panel signal telemetry", panel_labels, registry=registry),
        panel_temperature_celsius=Gauge("tigo_panel_temperature_celsius", "Latest panel temperature telemetry when available", panel_labels, registry=registry),
        panel_metric_value=Gauge("tigo_panel_metric_value", "Latest generic panel telemetry value for raw/unknown params", panel_labels + ["param"], registry=registry),
        panel_power_rating_watts=Gauge("tigo_panel_power_rating_watts", "Rated max power for the panel from system topology (watts)", panel_labels, registry=registry),
        inverter_power_watts=Gauge("tigo_inverter_power_watts", "Total DC power across all panels on this inverter (watts)", ["system_id", "inverter_id", "inverter_label"], registry=registry),
        string_power_watts=Gauge("tigo_string_power_watts", "Total DC power across all panels on this string (watts)", ["system_id", "inverter_id", "inverter_label", "string_id", "string_label"], registry=registry),
        system_power_rating_dc_watts=Gauge("tigo_system_power_rating_dc_watts", "Rated DC capacity of the system (watts)", ["system_id", "system_name"], registry=registry),
        system_power_rating_ac_watts=Gauge("tigo_system_power_rating_ac_watts", "Rated AC capacity of the system (watts)", ["system_id", "system_name"], registry=registry),
    )


def clear_labeled_metrics(metrics: Metrics) -> None:
    for gauge in (
        metrics.system_info,
        metrics.system_recent_alerts_count,
        metrics.system_has_monitored_modules,
        metrics.system_last_power_dc_watts,
        metrics.system_daily_energy_dc_watt_hours,
        metrics.system_ytd_energy_dc_watt_hours,
        metrics.system_lifetime_energy_dc_watt_hours,
        metrics.system_summary_updated_timestamp_seconds,
        metrics.source_info,
        metrics.source_gateway_count,
        metrics.source_panel_count,
        metrics.source_discovery_complete,
        metrics.source_last_checkin_timestamp_seconds,
        metrics.source_up,
        metrics.source_set_last_min_timestamp_seconds,
        metrics.source_set_last_day_timestamp_seconds,
        metrics.source_set_last_raw_timestamp_seconds,
        metrics.panel_info,
        metrics.panel_up,
        metrics.panel_last_telemetry_timestamp_seconds,
        metrics.panel_power_watts,
        metrics.panel_voltage_volts,
        metrics.panel_current_amps,
        metrics.panel_signal_strength,
        metrics.panel_temperature_celsius,
        metrics.panel_metric_value,
        metrics.panel_power_rating_watts,
        metrics.inverter_power_watts,
        metrics.string_power_watts,
        metrics.system_power_rating_dc_watts,
        metrics.system_power_rating_ac_watts,
    ):
        gauge.clear()
