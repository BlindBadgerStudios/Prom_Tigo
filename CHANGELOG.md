# Changelog

## 0.5.1 - 2026-10-27

### Added
- `GET /status` returns a read-only HTML page for operators, and `GET /status?format=json` returns the same fields. `/metrics` is unchanged and no Prometheus metric was renamed.
- Overall state is `UP`, `DEGRADED`, or `DOWN`, documented in `app/status_health.py`. UP means the last success is within 2× the poll interval and there is no auth failure. DEGRADED means an upstream 5xx, timeout, connection, or 429 while the poll thread is still backing off (this stays DEGRADED past the hard age cutoff). DOWN means an auth failure (401/403), a stopped poll thread, or no success past the hard age cutoff (15 minutes, or 3× the poll interval when that is longer) for any other failure.
- Tigo extras: site name without coordinates, panels present/expected, and a note that panel temperatures are omitted in cloud mode (not an error). A failed poll keeps the last successful snapshot.
- The HTML page notes that the exporter is reachable on docker3 at `http://127.0.0.1:<port>/status` via the lab SSH hop (no public listen).
- Status text redacts tokens, passwords, authorization headers, tokenized URLs, email addresses, and coordinates.

## 0.5.0 - 2026-10-27

### Changed
- Dependency bump: `prometheus-client` 0.24.1 → 0.26.0.
- Minor version bump to 0.5.0.

## 0.4.5 - 2026-10-13

- Bumped `pytigo` pin to 0.4.5. See git history for earlier changes.
