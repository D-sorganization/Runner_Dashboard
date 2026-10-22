#!/usr/bin/env bash
# Recover from transient self-hosted buildx flakes (stale ephemeral builders,
# containerd lease errors). Safe on CI: only removes builder-* / gm-ci-* names,
# never persistent shared builders (see deploy/runner-cleanup.sh).
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "docker CLI not available; skipping buildx recovery" >&2
  exit 0
fi

docker buildx version >/dev/null 2>&1 || exit 0

while IFS= read -r name; do
  [ -n "$name" ] || continue
  case "$name" in
    builder-*|gm-ci-*)
      docker buildx rm -f "$name" >/dev/null 2>&1 || true
      ;;
  esac
done < <(docker buildx ls --format '{{.Name}}' 2>/dev/null || true)

docker buildx prune -f >/dev/null 2>&1 || true
