# Pinned to a patch release + multi-arch index digest for reproducible builds.
# To update: bump the tag and replace the digest (docker buildx imagetools inspect python:<tag>).
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

# Runtime dependencies only (pytest and friends live in requirements-dev.txt).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

USER app

EXPOSE 10111

# No curl in the image: probe /metrics with the stdlib. Honors LISTEN_PORT.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/metrics' % os.environ.get('LISTEN_PORT', '10111'), timeout=5).read(1)"]

CMD ["python", "-m", "app.main"]
