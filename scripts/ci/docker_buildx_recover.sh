#!/usr/bin/env bash
# Recover only this run's builder. Other jobs share the Docker daemon; a
# builder-* prefix is not evidence that a builder is stale or belongs to us.
set -euo pipefail

mode="${1:-}"
if [ "$#" -gt 1 ] || { [ -n "$mode" ] && [ "$mode" != "--recreate" ]; }; then
  echo "usage: docker_buildx_recover.sh [--recreate]" >&2
  exit 2
fi

if [ -z "${BUILDX_BUILDER:-}" ]; then
  echo "No job-owned builder configured; skipping recovery" >&2
  exit 0
fi
if ! [[ "${GITHUB_RUN_ID:-}" =~ ^[0-9]+$ && "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]+$ ]]; then
  echo "Refusing recovery without a valid run and attempt identity" >&2
  exit 2
fi
expected="rd-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
if [ "$BUILDX_BUILDER" != "$expected" ]; then
  echo "Refusing recovery of a builder not owned by this run" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker CLI not available; skipping buildx recovery" >&2
  exit 0
fi

docker buildx version >/dev/null 2>&1 || exit 0

docker buildx rm -f "$expected" >/dev/null 2>&1 || true
if [ "$mode" = "--recreate" ]; then
  docker buildx create --name "$expected" --driver docker-container --use --bootstrap
fi
