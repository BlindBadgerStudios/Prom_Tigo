from app.config import load_config
from app.main import _build_client


def test_load_config_accepts_local_timezone(monkeypatch):
    monkeypatch.setenv('TIGO_MODE', 'local')
    monkeypatch.setenv('TIGO_LOCAL_HOST', '192.168.192.114')
    monkeypatch.setenv('TIGO_LOCAL_TIMEZONE', 'America/Phoenix')
    monkeypatch.delenv('TIGO_LOCAL_UTC_OFFSET', raising=False)
    monkeypatch.delenv('TIGO_LOCAL_TZ_OFFSET_SECONDS', raising=False)

    config = load_config()

    assert config.local_timezone == 'America/Phoenix'
    assert config.local_utc_offset == ''
    assert config.local_tz_offset_seconds == -25200


def test_build_client_passes_user_friendly_timezone_options(monkeypatch):
    monkeypatch.setenv('TIGO_MODE', 'local')
    monkeypatch.setenv('TIGO_LOCAL_HOST', '192.168.192.114')
    monkeypatch.setenv('TIGO_LOCAL_TIMEZONE', 'America/Phoenix')
    monkeypatch.setenv('TIGO_LOCAL_UTC_OFFSET', '-07:00')
    monkeypatch.setenv('TIGO_LOCAL_TZ_OFFSET_SECONDS', '-18000')

    config = load_config()
    client = _build_client(config)

    assert client.timezone_name == 'America/Phoenix'
    assert client.utc_offset == '-07:00'
    assert client.tz_offset_seconds == -25200
