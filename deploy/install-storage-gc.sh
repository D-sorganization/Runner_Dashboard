#!/usr/bin/env bash
# install-storage-gc.sh — the ONE installer for the fleet storage GC (#1925).
#
# Owns /usr/local/bin/runner-cleanup and the runner-cleanup / runner-disk-guard
# service+timer pairs. Every other installer (install-runner-maintenance.sh,
# fleet_storage_gc.py) delegates here, so the units are defined exactly once.
#
# Why it exists: on 2026-10-11 every runner host ran a DIFFERENT cleanup —
# OGLaptop an old fork with no rustup reaper (35k leaked dirs, C: at 0.6 GB),
# DeskComputer the right binary silently replaced by a drop-in ExecStart
# override — because three installers wrote the same paths and nothing ever
# checked what was actually installed.
#
# Contract
#   Preconditions  (violated => exit 2, nothing written):
#     known profile; valid, existing RUNNER_USER; absolute RUNNER_ROOTS;
#     source passes `bash -n`; root when targeting the real filesystem;
#     a sandbox root (STORAGE_GC_ROOT) must come with a SYSTEMCTL_BIN override.
#   Postconditions (checked by --verify after every install; exit 1 on drift):
#     binary == source (sha256) and matches the STAMP; all four units present
#     with the canonical ExecStart; no drop-in overrides ExecStart; both
#     timers enabled.
#   Idempotent: a second run rewrites nothing and reports changed=0.
#   Drop-ins that override ExecStart are neutralised, NOT deleted: the
#   original is kept as <file>.bak-storage-gc and its Environment= tuning
#   (e.g. the WAN bandwidth policy's RUNNER_WORK_DAYS=7) stays in force.
#
# Usage: install-storage-gc.sh [--profile runner|workstation] [--verify] [--dry-run]
# Env:   RUNNER_USER, RUNNER_ROOTS (colon list), STORAGE_GC_ROOT (sandbox, tests),
#        SYSTEMCTL_BIN, RUNNER_CLEANUP_SOURCE, STORAGE_GC_SOURCE_REV.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${STORAGE_GC_ROOT:-/}"
SOURCE="${RUNNER_CLEANUP_SOURCE:-${SCRIPT_DIR}/runner-cleanup.sh}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
RUNNER_USER="${RUNNER_USER:-${SUDO_USER:-$(id -un)}}"

BIN=/usr/local/bin/runner-cleanup
STAMP=/usr/local/share/runner-cleanup/STAMP
UNIT_DIR=/etc/systemd/system
SERVICES=(runner-cleanup.service runner-disk-guard.service)
TIMERS=(runner-cleanup.timer runner-disk-guard.timer)

PROFILE=runner
MODE=install
DRY_RUN=0
CHANGED=0

die_contract() { echo "install-storage-gc: $*" >&2; exit 2; }
say() { echo "install-storage-gc: $*"; }
p() { printf '%s%s' "${ROOT%/}" "$1"; }  # path under the (possibly sandboxed) root
sha() { sha256sum "$1" | cut -d' ' -f1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile) PROFILE="${2:-}"; shift 2 || die_contract "--profile needs a value" ;;
        --verify)  MODE=verify; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die_contract "unknown argument: $1" ;;
    esac
done

user_home() {
    local home=""
    command -v getent >/dev/null 2>&1 && home="$(getent passwd "$RUNNER_USER" | cut -d: -f6 || true)"
    [[ -n "$home" ]] || home="$(eval echo "~${RUNNER_USER}")"  # name validated by require_preconditions
    printf '%s' "$home"
}

require_preconditions() {
    [[ "$PROFILE" == runner || "$PROFILE" == workstation ]] || die_contract "unknown profile '$PROFILE' (runner|workstation)"
    [[ "$RUNNER_USER" =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ ]] || die_contract "invalid runner user name '$RUNNER_USER'"
    id -u -- "$RUNNER_USER" >/dev/null 2>&1 || die_contract "runner user '$RUNNER_USER' does not exist"
    RUNNER_ROOTS="${RUNNER_ROOTS:-$(user_home)/actions-runners}"
    local r
    IFS=: read -r -a _roots <<< "$RUNNER_ROOTS"
    [[ ${#_roots[@]} -gt 0 ]] || die_contract "RUNNER_ROOTS is empty"
    for r in "${_roots[@]}"; do
        [[ "$r" == /* ]] || die_contract "RUNNER_ROOTS entry '$r' must be absolute"
    done
    [[ -f "$SOURCE" ]] && bash -n "$SOURCE" 2>/dev/null || die_contract "source '$SOURCE' is missing or fails bash -n"
    if [[ "$ROOT" == "/" ]]; then
        [[ "$(id -u)" == 0 ]] || die_contract "must run as root to install into / (or set STORAGE_GC_ROOT to a sandbox)"
        [[ -d /run/systemd/system ]] || die_contract "systemd is not PID 1 on this host"
    elif [[ "$SYSTEMCTL_BIN" == systemctl ]]; then
        die_contract "a sandbox STORAGE_GC_ROOT requires a SYSTEMCTL_BIN override (refusing to touch the host's systemd)"
    fi
}

profile_env() {
    # Workstations: no /tmp reaping under a logged-in user, no wholesale
    # docker prune at 85%, stopped containers are user state.
    [[ "$PROFILE" == workstation ]] || return 0
    printf 'Environment=CLEANUP_TMP=0\nEnvironment=AGGRESSIVE_ON_PRESSURE=0\nEnvironment=PRUNE_DOCKER_CONTAINERS=0\n'
}

common_env() {
    printf 'Environment=RUNNER_ROOT=%s\nEnvironment=RUNNER_ROOTS=%s\nEnvironment=RUNNER_USER=%s\nEnvironment=STORAGE_GC_PROFILE=%s\n' \
        "${_roots[0]}" "$RUNNER_ROOTS" "$RUNNER_USER" "$PROFILE"
}

render_unit() {
    case "$1" in
        runner-cleanup.service)
            printf '[Unit]\nDescription=Clean stale GitHub runner, Docker, and WSL cache data\nAfter=docker.service\nWants=docker.service\n\n[Service]\nType=oneshot\nUser=root\n'
            common_env
            printf 'Environment=RUNNER_WORK_DAYS=3\nEnvironment=RUNNER_TEMP_DAYS=1\nEnvironment=TOOL_CACHE_DAYS=21\nEnvironment=DOCKER_PRUNE_UNTIL=168h\nEnvironment=PRUNE_DOCKER_VOLUMES=0\n'
            profile_env
            printf 'ExecStart=%s\n' "$BIN" ;;
        runner-cleanup.timer)
            printf '[Unit]\nDescription=Run GitHub runner cleanup daily\n\n[Timer]\nOnCalendar=*-*-* 04:20:00\nRandomizedDelaySec=30m\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n' ;;
        runner-disk-guard.service)
            printf '[Unit]\nDescription=Reclaim Docker/journal disk space under pressure (runner-safe)\nAfter=docker.service\nWants=docker.service\n\n[Service]\nType=oneshot\nUser=root\n'
            common_env
            printf 'Environment=DISK_PRESSURE_PERCENT=85\n'
            profile_env
            printf 'ExecStart=%s --disk-guard\n' "$BIN" ;;
        runner-disk-guard.timer)
            printf '[Unit]\nDescription=Run runner disk guard hourly\n\n[Timer]\nOnCalendar=hourly\nRandomizedDelaySec=5m\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n' ;;
    esac
}

# Write $2 to $1 only when it differs; atomic (tmp + mv). Counts changes.
write_if_changed() {
    local dest="$1" content="$2" mode="$3"
    if [[ -f "$dest" ]] && [[ "$(cat "$dest")" == "$content" ]]; then return 0; fi
    CHANGED=$((CHANGED + 1))
    if [[ "$DRY_RUN" == 1 ]]; then say "[dry-run] would write $dest"; return 0; fi
    mkdir -p "$(dirname "$dest")"
    printf '%s\n' "$content" > "$dest.tmp.$$"
    chmod "$mode" "$dest.tmp.$$"
    mv -f "$dest.tmp.$$" "$dest"
    say "wrote $dest"
}

install_binary() {
    local dest; dest="$(p "$BIN")"
    if [[ -f "$dest" ]] && cmp -s "$SOURCE" "$dest"; then return 0; fi
    CHANGED=$((CHANGED + 1))
    if [[ "$DRY_RUN" == 1 ]]; then say "[dry-run] would install $SOURCE -> $dest"; return 0; fi
    mkdir -p "$(dirname "$dest")"
    install -m 0755 "$SOURCE" "$dest.tmp.$$"
    mv -f "$dest.tmp.$$" "$dest"
    say "installed $dest (sha256 $(sha "$dest"))"
}

source_rev() {
    [[ -n "${STORAGE_GC_SOURCE_REV:-}" ]] && { printf '%s' "$STORAGE_GC_SOURCE_REV"; return; }
    git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || printf 'unknown'
}

dropins() {
    local svc f
    for svc in "${SERVICES[@]}"; do
        for f in "$(p "$UNIT_DIR")/$svc.d"/*.conf; do [[ -f "$f" ]] && printf '%s\n' "$f"; done
    done
    return 0
}

neutralise_overrides() {
    local f
    while IFS= read -r f; do
        grep -q '^ExecStart=' "$f" || continue
        CHANGED=$((CHANGED + 1))
        if [[ "$DRY_RUN" == 1 ]]; then say "[dry-run] would neutralise ExecStart override in $f"; continue; fi
        [[ -f "$f.bak-storage-gc" ]] || cp -p "$f" "$f.bak-storage-gc"
        grep -v '^ExecStart=' "$f" > "$f.tmp.$$" || true
        mv -f "$f.tmp.$$" "$f"
        say "neutralised ExecStart override in $f (original kept as $f.bak-storage-gc)"
    done < <(dropins)
}

check() {  # check NAME OK? DETAIL
    if [[ "$2" == 1 ]]; then echo "check=$1 status=ok"; else echo "check=$1 status=FAIL detail=$3"; VERIFY_OK=0; fi
}

verify() {
    VERIFY_OK=1
    local bin stamp unit want got
    bin="$(p "$BIN")"; stamp="$(p "$STAMP")"
    want="$( [[ -f "$SOURCE" ]] && sha "$SOURCE" || sed -n 's/^sha256=//p' "$stamp" 2>/dev/null )"
    got="$( [[ -f "$bin" ]] && sha "$bin" || echo missing )"
    check binary_hash "$([[ -n "$want" && "$got" == "$want" ]] && echo 1)" "installed=${got} expected=${want:-unknown}"
    check stamp "$(grep -qx "sha256=${got}" "$stamp" 2>/dev/null && echo 1)" "stamp missing or stale at $stamp"
    for unit in "${SERVICES[@]}" "${TIMERS[@]}"; do
        check "unit:$unit" "$([[ -f "$(p "$UNIT_DIR")/$unit" ]] && echo 1)" "missing"
    done
    check execstart "$(grep -qx "ExecStart=$BIN" "$(p "$UNIT_DIR")/runner-cleanup.service" 2>/dev/null \
        && grep -qx "ExecStart=$BIN --disk-guard" "$(p "$UNIT_DIR")/runner-disk-guard.service" 2>/dev/null && echo 1)" \
        "services do not run $BIN"
    local overrides; overrides="$(dropins | xargs -r grep -l '^ExecStart=' 2>/dev/null | tr '\n' ' ' || true)"
    check no_execstart_override "$([[ -z "$overrides" ]] && echo 1)" "${overrides% }"
    for unit in "${TIMERS[@]}"; do
        check "timer_enabled:$unit" "$([[ "$("$SYSTEMCTL_BIN" is-enabled "$unit" 2>/dev/null || true)" == enabled ]] && echo 1)" "not enabled"
    done
    if [[ "$VERIFY_OK" == 1 ]]; then echo "storage_gc_verify=ok"; return 0; fi
    echo "storage_gc_verify=fail"; return 1
}

if [[ "$MODE" == verify ]]; then verify; exit $?; fi

require_preconditions
install_binary
write_if_changed "$(p "$STAMP")" "$(printf 'sha256=%s\nprofile=%s\nsource_rev=%s' "$(sha "$SOURCE")" "$PROFILE" "$(source_rev)")" 0644
for unit in "${SERVICES[@]}" "${TIMERS[@]}"; do
    write_if_changed "$(p "$UNIT_DIR")/$unit" "$(render_unit "$unit")" 0644
done
neutralise_overrides
echo "changed=${CHANGED}"
if [[ "$DRY_RUN" == 1 ]]; then echo "storage_gc_verify=skipped (dry-run)"; exit 0; fi
"$SYSTEMCTL_BIN" daemon-reload
"$SYSTEMCTL_BIN" enable --now "${TIMERS[@]}" >/dev/null
verify
