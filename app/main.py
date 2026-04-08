from __future__ import annotations

import logging
import threading
import time

import requests.exceptions

from prometheus_client import start_http_server
from pytigo import TigoCCAClient, TigoClient, TigoClientProtocol

from .collector import TigoCollector
from .config import AppConfig, load_config
from .metrics import build_metrics


def _run_loop(collector: TigoCollector, poll_interval_seconds: int, metrics) -> None:
    while True:
        start = time.time()
        try:
            collector.collect_once()
            metrics.exporter_up.set(1)
            metrics.exporter_last_success_timestamp.set(time.time())
            logging.info("Poll succeeded in %.2fs", time.time() - start)
        except Exception:
            logging.exception("Polling cycle failed")
            metrics.exporter_errors_total.inc()
            metrics.exporter_up.set(0)
        finally:
            metrics.exporter_poll_duration.set(time.time() - start)
        time.sleep(poll_interval_seconds)


def _build_client(config: AppConfig) -> TigoClientProtocol:
    if config.mode == "local":
        return TigoCCAClient(
            host=config.local_host,
            username=config.local_username,
            password=config.local_password,
            timeout=config.timeout_seconds,
            tz_offset_seconds=config.local_tz_offset_seconds,
            enable_raw_temp_variants=config.local_enable_raw_temp_variants,
        )
    return TigoClient(
        username=config.username,
        password=config.password,
        timeout=config.timeout_seconds,
    )


_TRANSIENT_ERRORS = (
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
)


_LOGIN_RETRY_CAP_SECONDS = 60.0


def _login_with_retry(collector: TigoCollector, config: AppConfig) -> None:
    base_delay = config.rate_limit_base_delay_seconds
    attempt = 0
    while True:
        try:
            collector.login()
            return
        except _TRANSIENT_ERRORS as exc:
            delay = min(base_delay * (2 ** attempt), _LOGIN_RETRY_CAP_SECONDS)
            logging.warning("Login failed with transient error (%s), retrying in %.1fs (attempt %d)", exc, delay, attempt + 1)
            time.sleep(delay)
            attempt += 1
        except Exception:
            logging.exception("Login failed with non-retryable error")
            raise


def main() -> None:
    config = load_config()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    metrics = build_metrics()
    client = _build_client(config)
    collector = TigoCollector(client=client, config=config, metrics=metrics)
    _login_with_retry(collector, config)
    logging.info("Logged in to Tigo (%s mode). Starting exporter on port %d", config.mode, config.listen_port)
    start_http_server(config.listen_port, registry=metrics.registry)
    thread = threading.Thread(
        target=_run_loop,
        args=(collector, config.poll_interval_seconds, metrics),
        daemon=True,
    )
    thread.start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
