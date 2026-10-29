#!/usr/bin/env bash
# docker_smoke_test.sh — start the image docker-build.yml just built and
# confirm it actually comes up: /livez reports healthy and the SPA index is
# served. An image that builds but cannot start previously still passed CI
# (#3178). Always removes the container; dumps its logs first on any failure.
set -euo pipefail

usage() {
  echo "usage: docker_smoke_test.sh <image-tag> [host-port]" >&2
  exit 2
}

tag="${1:-}"
[ -n "$tag" ] || usage
port="${2:-18321}"

retries="${SMOKE_TEST_RETRIES:-20}"
sleep_s="${SMOKE_TEST_SLEEP:-1}"

container="rd-smoke-${GITHUB_RUN_ID:-local}-$$"
failed=0

cleanup() {
  local status=$?
  if [ "$status" -ne 0 ] || [ "$failed" -ne 0 ]; then
    echo "::group::Container logs ($container)"
    docker logs "$container" 2>&1 || true
    echo "::endgroup::"
  fi
  docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$container" -p "${port}:8321" "$tag" >/dev/null

healthy=""
attempt=1
while [ "$attempt" -le "$retries" ]; do
  if curl -fsS "http://localhost:${port}/livez" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep "$sleep_s"
  attempt=$((attempt + 1))
done
if [ -z "$healthy" ]; then
  failed=1
  echo "::error::Container never reported healthy on /livez after ${retries} attempts" >&2
  exit 1
fi

if ! curl -fsS "http://localhost:${port}/" | grep -q 'id="root"'; then
  failed=1
  echo "::error::UI index was not served from the running image" >&2
  exit 1
fi

echo "Docker image smoke test passed: /livez healthy, UI index served."
