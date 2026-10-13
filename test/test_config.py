import logging

from app.config import AppConfig, load_config, validate_panel_telemetry_params
from app.main import _build_client


def test_load_config_accepts_local_timezone(monkeypatch):
    monkeypatch.setenv("TIGO_MODE", "local")
    monkeypatch.setenv("TIGO_LOCAL_HOST", "192.168.1.50")
    monkeypatch.setenv("TIGO_LOCAL_TIMEZONE", "America/Phoenix")
    monkeypatch.delenv("TIGO_LOCAL_UTC_OFFSET", raising=False)
    monkeypatch.delenv("TIGO_LOCAL_TZ_OFFSET_SECONDS", raising=False)

    config = load_config()

    assert config.local_timezone == "America/Phoenix"
    assert config.local_utc_offset == ""
    assert config.local_tz_offset_seconds == -25200


def test_build_client_passes_user_friendly_timezone_options(monkeypatch):
    monkeypatch.setenv("TIGO_MODE", "local")
    monkeypatch.setenv("TIGO_LOCAL_HOST", "192.168.1.50")
    monkeypatch.setenv("TIGO_LOCAL_TIMEZONE", "America/Phoenix")
    monkeypatch.setenv("TIGO_LOCAL_UTC_OFFSET", "-07:00")
    monkeypatch.setenv("TIGO_LOCAL_TZ_OFFSET_SECONDS", "-18000")

    config = load_config()
    client = _build_client(config)

    assert client.timezone_name == "America/Phoenix"
    assert client.utc_offset == "-07:00"
    assert client.tz_offset_seconds == -25200


def _cloud_env(monkeypatch):
    monkeypatch.setenv("TIGO_MODE", "cloud")
    monkeypatch.setenv("TIGO_USERNAME", "user@example.com")
    monkeypatch.setenv("TIGO_PASSWORD", "test-pass")


def test_cloud_default_params_exclude_temperature(monkeypatch):
    _cloud_env(monkeypatch)
    monkeypatch.delenv("PANEL_TELEMETRY_PARAMS", raising=False)

    config = load_config()

    assert config.panel_telemetry_params == ["Pin", "Vin", "Iin", "RSSI"]
    assert AppConfig().panel_telemetry_params == ["Pin", "Vin", "Iin", "RSSI"]


def test_cloud_mode_skips_unknown_params_with_warning(monkeypatch, caplog):
    _cloud_env(monkeypatch)
    monkeypatch.setenv("PANEL_TELEMETRY_PARAMS", "Pin,Temp,Vin,Tmod,Inverter_AC,Pin")

    with caplog.at_level(logging.WARNING, logger="app.config"):
        config = load_config()

    assert config.panel_telemetry_params == ["Pin", "Vin", "Inverter_AC"]
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("'Temp'" in m for m in warned)
    assert any("'Tmod'" in m for m in warned)


def test_local_mode_params_are_not_validated(monkeypatch):
    monkeypatch.setenv("TIGO_MODE", "local")
    monkeypatch.setenv("TIGO_LOCAL_HOST", "192.168.1.50")
    monkeypatch.setenv("PANEL_TELEMETRY_PARAMS", "Pin,Tmod,Power")

    assert validate_panel_telemetry_params("local", ["Pin", "Tmod"]) == ["Pin", "Tmod"]
    assert load_config().panel_telemetry_params == ["Pin", "Tmod", "Power"]
