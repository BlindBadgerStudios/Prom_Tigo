from __future__ import annotations

import logging
import threading
import time

from prometheus_client import start_http_server
from pytigo import TigoClient

from .collector import TigoCollector
from .config import load_config
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


def main() -> None:
    config = load_config()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    metrics = build_metrics()
    client = TigoClient(
        username=config.username,
        password=config.password,
        timeout=config.timeout_seconds,
    )
    collector = TigoCollector(client=client, config=config, metrics=metrics)
    collector.login()
    logging.info("Logged in to Tigo. Starting exporter on port %d", config.listen_port)
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
