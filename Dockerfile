# Dockerfile for runner-dashboard
# Provides a reproducible, hardened container environment.
#
# Base image: python:3.13-slim
# Keep the image on the newest runtime allowed by pyproject.toml
# (requires-python = ">=3.11,<3.14") so locked native wheels are used instead
# of slow CPython 3.14 source builds.
# To regenerate requirements.lock.txt:  uv export --no-dev -o requirements.lock.txt

FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc

WORKDIR /app

# Install system dependencies (curl needed for HEALTHCHECK)
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and group
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /sbin/nologin appuser

# Copy requirements first for layer caching; install with hash verification.
COPY requirements.lock.txt .
RUN pip install --no-cache-dir --upgrade \
        pip==26.1.2 \
        setuptools==82.0.1 \
        wheel==0.47.0 \
        jaraco.context==6.1.2 && \
    pip install --no-cache-dir --require-hashes -r requirements.lock.txt && \
    rm -rf /usr/local/lib/python3.13/site-packages/wheel-0.45.1.dist-info \
           /usr/local/lib/python3.13/site-packages/jaraco.context-5.3.0.dist-info \
           /usr/local/lib/python3.13/site-packages/jaraco_context-5.3.0.dist-info \
           /usr/local/lib/python3.13/site-packages/pip-25.0.1.dist-info \
           /usr/local/lib/python3.13/site-packages/setuptools-79.0.1.dist-info \
           /usr/local/lib/python3.13/site-packages/setuptools/_vendor/jaraco.context-5.3.0.dist-info \
           /usr/local/lib/python3.13/site-packages/setuptools/_vendor/wheel-0.45.1.dist-info \
           /root/.cache \
           /tmp/* \
           /var/tmp/*


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
