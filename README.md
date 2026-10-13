# Prom_Tigo

Prometheus exporter for near real-time Tigo solar telemetry using the PyTigo client.

The exporter is intentionally focused on Prometheus-friendly current state rather than
historical reporting. It polls Tigo's official API v3 through PyTigo, converts the most
recent available system, source, and panel telemetry into scrapeable gauges, and exposes
them over HTTP for Prometheus.

Current priorities:
- system and gateway status/freshness
- recent alerts visibility
- panel telemetry such as watts, volts, amps, and RSSI (panel temperature is not available from the cloud API; see [Panel temperature](#panel-temperature))
- topology labels that make Grafana dashboards and PromQL joins practical

## Metrics focus

Exporter health:
- `tigo_exporter_up`
- `tigo_exporter_last_success_timestamp_seconds`
- `tigo_exporter_poll_duration_seconds`
- `tigo_exporter_errors_total`
- `tigo_exporter_api_errors_total{endpoint,param,status}` - failed Tigo API calls. `endpoint` is
  `data/aggregate`, `alerts/system` or `poll` (a whole poll failed); `param` is the telemetry
  param for `data/aggregate`; `status` is the HTTP status code or an error type such as
  `timeout` or `ConnectionError`.

`tigo_exporter_up` is `0` from startup until the first successful poll.

### Missing values are absent, not 0

A panel series is only exported when the API returned a value for it in the current poll.
If a param fails (HTTP 400/429/5xx, timeout) or a panel has no sample in the window, that
series is **absent** for that poll instead of reading `0`. A real `0` from the API (for example
at night) is still exported as `0`. Failed params are logged once per poll at WARNING and counted
in `tigo_exporter_api_errors_total`. Use `tigo_panel_up` and
`tigo_panel_last_telemetry_timestamp_seconds` to detect stale panels, and remember that sums
such as `tigo_inverter_power_watts` only include panels that reported in that poll.

### Failure handling

- After a failed poll the exporter waits with exponential backoff and jitter (poll interval,
  then 2x, 4x, ... up to 15 minutes) instead of the fixed interval. On HTTP 429 the
  `Retry-After` header is a minimum. The normal interval resumes after a successful poll.
- The exporter logs in again only after HTTP 401/403. Other errors (429, 400, 5xx, timeouts)
  keep the existing session.
- Inside a poll, a rate-limited `data/aggregate` call is retried up to `RATE_LIMIT_MAX_RETRIES`
  times, honoring `Retry-After` (capped at 60 s per retry).

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

Tigo cloud API v3 `data/aggregate` only accepts these `param` values:
`Pin`, `Vin`, `Iin`, `RSSI`, `Inv_AC`, `Inverter_AC`. Any other value is rejected with
HTTP 400. Any param that does not map cleanly to a dedicated metric is still exported through
`tigo_panel_metric_value{param=...}`.

Default `PANEL_TELEMETRY_PARAMS` (when the variable is unset):
- cloud mode: `Pin,Vin,Iin,RSSI`
- local mode: `Pin,Vin,Iin,RSSI` (plus `Tmod,Tcell,Tamb` when `TIGO_LOCAL_RAW_TEMP_VARIANTS=true`)

In cloud mode the exporter validates `PANEL_TELEMETRY_PARAMS` at startup: any entry outside
`Pin`, `Vin`, `Iin`, `RSSI`, `Inv_AC`, `Inverter_AC` is logged at WARNING and skipped, so it
never costs an API call. Local mode is not validated: the local CCA client returns no data for
params it does not support, so those series are simply absent.

## Panel temperature

**Known limitation: in cloud mode, `tigo_panel_temperature_celsius` is not emitted.**
Earlier versions exported it as a constant `0` placeholder; dashboards or alerts that read it
will now see no data in cloud mode.

- Tigo cloud API v3 does not expose panel temperature. `data/aggregate` rejects `Temp`,
  `Tmod`, `Tcell`, `Tamb` and other temperature params with HTTP 400 (`param must be one of
  the following values: ...`), and `objects/system` contains no temperature object
  (verified 2026-10-12 against a live system).
- Cloud mode skips temperature params (see above), so neither
  `tigo_panel_temperature_celsius` nor `tigo_panel_metric_value{param="Temp"...}` is exported.
  `Temp`, `Tmod`, `Tcell`, and `Tamb` all feed the same `tigo_panel_temperature_celsius` gauge
  when they are polled (local mode).
- Panel temperature has only been seen with local CCA access. Local support is still
  exploratory: on the CCA tested by PyTigo, `temp=tmod`/`tcell`/`tamb` returned the same
  payload as `pin` (power), and `temp=temp` returned an empty body. With
  `TIGO_LOCAL_RAW_TEMP_VARIANTS=true` those values feed `tigo_panel_temperature_celsius`,
  so check that they differ from `tigo_panel_power_watts` before you treat them as temperatures.
  Local mode leaves the temperature params out by default.

Cloud users who still list `Temp`, `Tmod`, `Tcell`, or `Tamb` in `PANEL_TELEMETRY_PARAMS` will
see a startup WARNING; remove them to silence it.

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
- `PANEL_TELEMETRY_PARAMS` - comma-separated telemetry params to request (cloud accepts only `Pin`, `Vin`, `Iin`, `RSSI`, `Inv_AC`, `Inverter_AC`; see [Panel temperature](#panel-temperature))
- `SOURCE_STALE_AFTER_SECONDS` - default `900`
- `PANEL_STALE_AFTER_SECONDS` - default `900`
- `ALERT_FETCH_LIMIT` - default `200`

Local mode extras:
- `TIGO_MODE=local`
- `TIGO_LOCAL_HOST`
- `TIGO_LOCAL_USERNAME` - default `Tigo`
- `TIGO_LOCAL_PASSWORD` - required in local mode (`<cca-password>`; the app falls back to the CCA vendor default if unset)
- `TIGO_LOCAL_TIMEZONE` - optional IANA timezone name like `America/Los_Angeles` (recommended)
- `TIGO_LOCAL_UTC_OFFSET` - optional offset string like `-07:00`
- `TIGO_LOCAL_TZ_OFFSET_SECONDS` - legacy raw offset fallback; default `0`
- `TIGO_LOCAL_RAW_TEMP_VARIANTS` - default `false`; adds exploratory `Tmod`, `Tcell`, `Tamb` (see [Panel temperature](#panel-temperature))

## Prometheus scrape config example

```yaml
- job_name: tigo
  scrape_interval: 60s
  scrape_timeout: 15s
  static_configs:
    - targets:
        - 192.168.1.10:10111
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

The image runs as a non-root `app` user, is based on a digest-pinned
`python:3.12.15-slim-bookworm`, ships only runtime dependencies (`requirements.txt`; test tools
are in `requirements-dev.txt`), and has a `HEALTHCHECK` that fetches `/metrics` with Python's
`urllib` (it honors `LISTEN_PORT`).

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
python -m app.main
```

## Secret scanning

This repo uses a [gitleaks](https://github.com/gitleaks/gitleaks) pre-commit hook. After cloning, run:

```sh
pip install pre-commit  # or: uv tool install pre-commit
pre-commit install
```
