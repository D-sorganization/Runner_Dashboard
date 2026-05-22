# Dockerfile for runner-dashboard
# Provides a reproducible, hardened container environment.
#
# Base image: python:3.11.10-slim pinned to a Docker Hub sha256 digest.
# To regenerate requirements.lock.txt:  uv export --no-dev -o requirements.lock.txt

FROM python:3.11.10-slim@sha256:efc99f05ec45381aac55e2803c9a0245ea5b8c74965264498338e24e4bf66cc7

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
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt && \
    pip install --no-cache-dir setuptools==80.9.0 wheel==0.46.2 jaraco.context==6.1.0 && \
    rm -rf /usr/local/lib/python3.11/site-packages/wheel-0.45.1.dist-info \
           /usr/local/lib/python3.11/site-packages/jaraco.context-5.3.0.dist-info \
           /usr/local/lib/python3.11/site-packages/jaraco_context-5.3.0.dist-info \
           /usr/local/lib/python3.11/site-packages/setuptools-65.5.1.dist-info \
           /usr/local/lib/python3.11/site-packages/setuptools/_vendor/jaraco.context-5.3.0.dist-info \
           /usr/local/lib/python3.11/site-packages/setuptools/_vendor/wheel-0.45.1.dist-info


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
