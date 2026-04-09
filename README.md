# Prom_Tigo

Prometheus exporter for near real-time Tigo solar telemetry using the PyTigo client.

The exporter is intentionally focused on Prometheus-friendly current state rather than
historical reporting. It polls Tigo's official API v3 through PyTigo, converts the most
recent available system, source, and panel telemetry into scrapeable gauges, and exposes
them over HTTP for Prometheus.

Current priorities:
- system and gateway status/freshness
- recent alerts visibility
- panel telemetry such as watts, volts, amps, RSSI, and optionally temperature-like fields
- topology labels that make Grafana dashboards and PromQL joins practical

## Metrics focus

Exporter health:
- `tigo_exporter_up`
- `tigo_exporter_last_success_timestamp_seconds`
- `tigo_exporter_poll_duration_seconds`
- `tigo_exporter_errors_total`

System/source status:
- `tigo_system_info`
- `tigo_system_recent_alerts_count`
- `tigo_system_has_monitored_modules`
- `tigo_system_last_power_dc_watts`
- `tigo_source_info`
- `tigo_source_last_checkin_timestamp_seconds`
- `tigo_source_up`
- `tigo_source_discovery_complete`
- `tigo_source_gateway_count`
- `tigo_source_panel_count`
- `tigo_source_set_last_min_timestamp_seconds`
- `tigo_source_set_last_day_timestamp_seconds`
- `tigo_source_set_last_raw_timestamp_seconds`

Topology/panel identity:
- `tigo_panel_info`
- `tigo_panel_up`
- `tigo_panel_last_telemetry_timestamp_seconds`

In local CCA mode, panel telemetry series are emitted for every known panel from topology even after a panel stops reporting in the freshest CCA sample. When a panel drops out of the latest sample, the exporter keeps publishing its telemetry gauges at `0` and sets `tigo_panel_up` to `0`, while `tigo_panel_last_telemetry_timestamp_seconds` preserves the last timestamp where telemetry was actually seen.

Panel telemetry:
- `tigo_panel_power_watts`
- `tigo_panel_voltage_volts`
- `tigo_panel_current_amps`
- `tigo_panel_signal_strength`
- `tigo_panel_temperature_celsius`
- `tigo_panel_metric_value{param=...}` as a generic fallback for raw/unknown telemetry params

## Important note on telemetry params

The documented Tigo aggregate parameters clearly cover `Pin`, `Vin`, `Iin`, and `RSSI`.
Temperature-related parameters can vary by installation or endpoint behavior, so this exporter
supports a configurable list of telemetry params and treats temperature aliases as best-effort.
Any param that does not map cleanly to a dedicated metric is still exported through
`tigo_panel_metric_value{param=...}`.

Default telemetry params:
- `Pin`
- `Vin`
- `Iin`
- `RSSI`
- `Temp`
- `Tmod`
- `Tcell`
- `Tamb`

## Environment variables

Required:
- `TIGO_USERNAME`
- `TIGO_PASSWORD`

Optional:
- `TIGO_SYSTEM_ID` - if omitted, the exporter selects the first accessible system
- `POLL_INTERVAL_SECONDS` - default `60`
- `LISTEN_PORT` - default `10111`
- `LOG_LEVEL` - default `INFO`
- `TIGO_TIMEOUT_SECONDS` - default `30`
- `TOPOLOGY_REFRESH_INTERVAL_POLLS` - default `10`
- `PANEL_TELEMETRY_WINDOW_MINUTES` - default `15`
- `PANEL_TELEMETRY_PARAMS` - comma-separated telemetry params to request
- `SOURCE_STALE_AFTER_SECONDS` - default `900`
- `PANEL_STALE_AFTER_SECONDS` - default `900`
- `ALERT_FETCH_LIMIT` - default `200`

## Prometheus scrape config example

```yaml
- job_name: tigo
  scrape_interval: 60s
  scrape_timeout: 15s
  static_configs:
    - targets:
        - 192.168.192.52:10111
      labels:
        site: home
        environment: prod
        role: energy
        service: tigo
        source: cloud
```

## Docker Compose configuration notes

Use `docker-compose.yaml` when you want to self-build the exporter image from source.
Use `compose.yaml` when you want to deploy a pre-built image.

The container is configured with:
- read-only root filesystem
- tmpfs for `/tmp`
- dropped Linux capabilities
- `no-new-privileges`

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
python -m app.main
```
