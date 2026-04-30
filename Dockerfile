# Dockerfile for runner-dashboard
# Provides a reproducible, hardened container environment.
#
# Base image digest is pinned to python:3.11.14-slim (multi-arch index).
# To regenerate:  docker pull python:3.11.14-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.11.14-slim
# To regenerate requirements.lock.txt:  pip-compile --generate-hashes --output-file requirements.lock.txt requirements.txt

FROM python:3.11.14-slim@sha256:c8271b1f627d0068857dce5b53e14a9558603b527e46f1f901722f935b786a39

WORKDIR /app

# Install system dependencies (curl needed for HEALTHCHECK)
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and group
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /sbin/nologin appuser

# Copy requirements first for layer caching; install with hash verification
COPY requirements.lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt
RUN pip uninstall -y wheel jaraco.context && pip install --no-cache-dir wheel==0.46.2 jaraco.context==6.1.0

# Copy application code and set ownership
COPY --chown=appuser:appuser backend/ ./backend/
COPY --chown=appuser:appuser config/ ./config/
COPY --chown=appuser:appuser frontend/ ./frontend/

# Environment defaults
ENV PYTHONPATH=/app
ENV DASHBOARD_PORT=8321

# Drop privileges — run as non-root (UID 10001)
USER 10001

EXPOSE 8321

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8321/livez || exit 1

CMD ["python", "-m", "backend.server"]
