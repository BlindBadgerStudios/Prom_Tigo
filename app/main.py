from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass

from prometheus_client import start_http_server
from pytigo import TigoCCAClient, TigoClient, TigoClientProtocol

from .collector import TigoCollector
from .config import AppConfig, load_config
from .errors import http_status, is_auth_error, retry_after_seconds, status_label
from .metrics import build_metrics


def _build_client(config: AppConfig) -> TigoClientProtocol:
    if config.mode == "local":
        return TigoCCAClient(
            host=config.local_host,
            username=config.local_username,
            password=config.local_password,
            timeout=config.timeout_seconds,
            tz_offset_seconds=config.local_tz_offset_seconds,
            timezone_name=config.local_timezone or None,
            utc_offset=config.local_utc_offset or None,
            enable_raw_temp_variants=config.local_enable_raw_temp_variants,
        )
    return TigoClient(
        username=config.username,
        password=config.password,
        timeout=config.timeout_seconds,
    )


# Cap for the failure backoff; the normal cadence resumes after the next success.
MAX_BACKOFF_SECONDS = 900.0


@dataclass
class LoopState:
    logged_in: bool = False
    consecutive_failures: int = 0


def _failure_delay(
    poll_interval_seconds: float,
    consecutive_failures: int,
    retry_after: float | None = None,
    *,
    max_backoff: float = MAX_BACKOFF_SECONDS,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with jitter after a failed poll, honoring Retry-After.

    The ceiling doubles per consecutive failure (interval, 2x, 4x, ... capped at
    max_backoff) and the actual delay is drawn from [ceiling/2, ceiling] so several
    exporters do not retry in lockstep. A Retry-After from a 429 is a floor.
    """
    rng = rng or random
    base = max(float(poll_interval_seconds), 1.0)
    exponent = min(max(consecutive_failures - 1, 0), 16)
    ceiling = min(base * (2 ** exponent), max(max_backoff, base))
    delay = rng.uniform(ceiling / 2, ceiling)
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


def _run_cycle(
    collector: TigoCollector,
    poll_interval_seconds: int,
    metrics,
    state: LoopState,
    *,
    rng: random.Random | None = None,
) -> float:
    """Run one poll and return how long to sleep before the next one."""
    start = time.time()
    try:
        if not state.logged_in:
            collector.login()
            state.logged_in = True
        collector.collect_once()
    except Exception as exc:
        state.consecutive_failures += 1
        # Only auth errors invalidate the session; anything else (429, 5xx, timeouts,
        # parse errors) keeps the token so we don't create a new login per failure.
        if is_auth_error(exc):
            state.logged_in = False
        retry_after = retry_after_seconds(exc) if http_status(exc) == 429 else None
        delay = _failure_delay(poll_interval_seconds, state.consecutive_failures, retry_after, rng=rng)
        logging.exception(
            "Polling cycle failed (%s, %d consecutive); next attempt in %.0fs%s",
            status_label(exc),
            state.consecutive_failures,
            delay,
            ", re-login scheduled" if not state.logged_in else "",
        )
        metrics.exporter_errors_total.inc()
        metrics.exporter_api_errors_total.labels(endpoint="poll", param="", status=status_label(exc)).inc()
        metrics.exporter_up.set(0)
        return delay
    else:
        state.consecutive_failures = 0
        metrics.exporter_up.set(1)
        metrics.exporter_last_success_timestamp.set(time.time())
        logging.info("Poll succeeded in %.2fs", time.time() - start)
        return float(poll_interval_seconds)
    finally:
        metrics.exporter_poll_duration.set(time.time() - start)


def _run_loop(collector: TigoCollector, poll_interval_seconds: int, metrics) -> None:
    state = LoopState()
    while True:
        time.sleep(_run_cycle(collector, poll_interval_seconds, metrics, state))


def main() -> None:
    # Configure logging before load_config() so config validation warnings use the normal format.
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = load_config()
    metrics = build_metrics()
    client = _build_client(config)
    collector = TigoCollector(client=client, config=config, metrics=metrics)
    start_http_server(config.listen_port, registry=metrics.registry)
    logging.info("Starting Tigo exporter on port %d (%s mode)", config.listen_port, config.mode)
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
