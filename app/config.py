from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


def _env_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else None


def _env_csv(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(slots=True)
class AppConfig:
    username: str
    password: str
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


def load_config() -> AppConfig:
    username = os.getenv("TIGO_USERNAME")
    password = os.getenv("TIGO_PASSWORD")
    if not username or not password:
        raise RuntimeError("TIGO_USERNAME and TIGO_PASSWORD must be set")
    return AppConfig(
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
            ["Pin", "Vin", "Iin", "RSSI", "Temp", "Tmod", "Tcell", "Tamb"],
        ),
        source_stale_after_seconds=_env_int("SOURCE_STALE_AFTER_SECONDS", 900),
        panel_stale_after_seconds=_env_int("PANEL_STALE_AFTER_SECONDS", 900),
        alert_fetch_limit=_env_int("ALERT_FETCH_LIMIT", 200),
    )
