#!/usr/bin/env bash
# Install restic rest-server on DeskComputer (WSL/system systemd). Does not create
# htpasswd or ACL changes — see docs/runbooks/fleet-restic-backup.md (#4016).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESTIC_REST_SERVER_VERSION="${RESTIC_REST_SERVER_VERSION:-0.13.0}"
INSTALL_PREFIX="${INSTALL_PREFIX:-/usr/local}"
UNIT_DEST="${UNIT_DEST:-/etc/systemd/system/restic-rest-server.service}"
ENV_DIR="${ENV_DIR:-/etc/restic-rest-server}"
DATA_DIR="${RESTIC_REST_DATA_DIR:-/var/lib/restic-rest-server/data}"

info() { echo "[fleet-backup] $*"; }
fail() { echo "[fleet-backup] ERROR: $*" >&2; exit 1; }

if [[ "$(id -u)" -ne 0 ]]; then
    fail "run as root (sudo)"
fi

ARCH="$(uname -m)"
case "${ARCH}" in
    x86_64) GOARCH=amd64 ;;
    aarch64|arm64) GOARCH=arm64 ;;
    *) fail "unsupported architecture: ${ARCH}" ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

TARBALL="rest-server_${RESTIC_REST_SERVER_VERSION}_linux_${GOARCH}.tar.gz"
URL="https://github.com/restic/rest-server/releases/download/v${RESTIC_REST_SERVER_VERSION}/${TARBALL}"
info "Downloading ${URL}"
curl -fsSL -o "${TMP}/${TARBALL}" "${URL}"
tar -xzf "${TMP}/${TARBALL}" -C "${TMP}"
install -m 0755 "${TMP}/rest-server" "${INSTALL_PREFIX}/bin/rest-server"

install -d -m 0750 "${DATA_DIR}"
install -d -m 0750 "${ENV_DIR}"
if [[ ! -f "${ENV_DIR}/env" ]]; then
    install -m 0600 "${SCRIPT_DIR}/rest-server.env.example" "${ENV_DIR}/env"
    info "Wrote ${ENV_DIR}/env from example — edit paths before start"
fi

install -m 0755 "${SCRIPT_DIR}/rest-server-run.sh" "${INSTALL_PREFIX}/bin/restic-rest-server-run.sh"

sed \
    -e "s|@ENV_FILE@|${ENV_DIR}/env|g" \
    -e "s|@RUN_SCRIPT@|${INSTALL_PREFIX}/bin/restic-rest-server-run.sh|g" \
    "${SCRIPT_DIR}/systemd/restic-rest-server.service" >"${TMP}/restic-rest-server.service"
install -m 0644 "${TMP}/restic-rest-server.service" "${UNIT_DEST}"

systemctl daemon-reload
info "Installed. Next: create ${ENV_DIR}/htpasswd, apply Tailscale ACL, then:"
info "  systemctl enable --now restic-rest-server.service"
