from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


def _env_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else None


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value not in (None, "") else default


def _env_csv(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_utc_offset_seconds(value: str) -> int:
    sign = 1 if value[0] == '+' else -1
    hours = int(value[1:3])
    minutes = int(value[4:6])
    return sign * ((hours * 3600) + (minutes * 60))


def _resolve_local_tz_offset_seconds(timezone_name: str, utc_offset: str, legacy_seconds: int) -> int:
    if timezone_name:
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise RuntimeError(f"Invalid TIGO_LOCAL_TIMEZONE: {timezone_name!r}") from exc
        offset = datetime.now(zone).utcoffset()
        return int(offset.total_seconds()) if offset is not None else 0
    if utc_offset:
        if len(utc_offset) != 6 or utc_offset[0] not in '+-' or utc_offset[3] != ':':
            raise RuntimeError(f"Invalid TIGO_LOCAL_UTC_OFFSET: {utc_offset!r}; expected ±HH:MM")
        return _parse_utc_offset_seconds(utc_offset)
    return legacy_seconds


@dataclass(slots=True)
class AppConfig:
    # --- shared ---
    mode: str = "cloud"          # "cloud" or "local"
    system_id: int | None = None
    poll_interval_seconds: int = 60
    listen_port: int = 10111
    log_level: str = "INFO"
    timeout_seconds: int = 30
    topology_refresh_interval_polls: int = 10
    panel_telemetry_window_minutes: int = 15
    panel_telemetry_params: list[str] = field(default_factory=lambda: ["Pin", "Vin", "Iin", "RSSI", "Temp", "Tmod", "Tcell", "Tamb"])
    source_stale_after_seconds: int = 900
    panel_stale_after_seconds: int = 900
    alert_fetch_limit: int = 200
    rate_limit_max_retries: int = 3
    rate_limit_base_delay_seconds: float = 5.0
    # --- cloud mode ---
    username: str = ""
    password: str = ""
    # --- local mode ---
    local_host: str = ""
    local_username: str = "Tigo"
    local_password: str = "$olar"
    local_timezone: str = ""
    local_utc_offset: str = ""
    local_tz_offset_seconds: int = 0
    local_enable_raw_temp_variants: bool = False


def load_config() -> AppConfig:
    mode = os.getenv("TIGO_MODE", "cloud").lower()
    if mode not in ("cloud", "local"):
        raise RuntimeError(f"TIGO_MODE must be 'cloud' or 'local', got {mode!r}")

    if mode == "cloud":
        username = os.getenv("TIGO_USERNAME")
        password = os.getenv("TIGO_PASSWORD")
        if not username or not password:
            raise RuntimeError("TIGO_USERNAME and TIGO_PASSWORD must be set in cloud mode")
    else:
        username = os.getenv("TIGO_USERNAME", "")
        password = os.getenv("TIGO_PASSWORD", "")

    if mode == "local":
        local_host = os.getenv("TIGO_LOCAL_HOST")
        if not local_host:
            raise RuntimeError("TIGO_LOCAL_HOST must be set in local mode")
    else:
        local_host = os.getenv("TIGO_LOCAL_HOST", "")

    local_timezone = os.getenv("TIGO_LOCAL_TIMEZONE", "")
    local_utc_offset = os.getenv("TIGO_LOCAL_UTC_OFFSET", "")
    local_tz_offset_seconds = _resolve_local_tz_offset_seconds(
        local_timezone,
        local_utc_offset,
        _env_int("TIGO_LOCAL_TZ_OFFSET_SECONDS", 0),
    )

    return AppConfig(
        mode=mode,
        username=username,
        password=password,
        system_id=_env_optional_int("TIGO_SYSTEM_ID"),
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 60),
        listen_port=_env_int("LISTEN_PORT", 10111),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        timeout_seconds=_env_int("TIGO_TIMEOUT_SECONDS", 30),
        topology_refresh_interval_polls=_env_int("TOPOLOGY_REFRESH_INTERVAL_POLLS", 10),
        panel_telemetry_window_minutes=_env_int("PANEL_TELEMETRY_WINDOW_MINUTES", 15),
        panel_telemetry_params=_env_csv(
            "PANEL_TELEMETRY_PARAMS",
            ["Pin", "Vin", "Iin", "RSSI", "Temp", "Tmod", "Tcell", "Tamb"] if mode == "cloud"
            else ["Pin", "Vin", "Iin", "RSSI", "Tmod", "Tcell", "Tamb"] if os.getenv("TIGO_LOCAL_RAW_TEMP_VARIANTS", "").lower() in ("1", "true", "yes")
            else ["Pin", "Vin", "Iin", "RSSI"],
        ),
        source_stale_after_seconds=_env_int("SOURCE_STALE_AFTER_SECONDS", 900),
        panel_stale_after_seconds=_env_int("PANEL_STALE_AFTER_SECONDS", 900),
        alert_fetch_limit=_env_int("ALERT_FETCH_LIMIT", 200),
        rate_limit_max_retries=_env_int("RATE_LIMIT_MAX_RETRIES", 3),
        rate_limit_base_delay_seconds=_env_float("RATE_LIMIT_BASE_DELAY_SECONDS", 5.0),
        local_host=local_host,
        local_username=os.getenv("TIGO_LOCAL_USERNAME", "Tigo"),
        local_password=os.getenv("TIGO_LOCAL_PASSWORD", "$olar"),
        local_timezone=local_timezone,
        local_utc_offset=local_utc_offset,
        local_tz_offset_seconds=local_tz_offset_seconds,
        local_enable_raw_temp_variants=os.getenv("TIGO_LOCAL_RAW_TEMP_VARIANTS", "").lower() in ("1", "true", "yes"),
    )
