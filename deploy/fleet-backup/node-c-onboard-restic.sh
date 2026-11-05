#!/usr/bin/env bash
# node-c restic client onboarding (#4010). Defaults to dry-run; set RESTIC_DRY_RUN=0 to apply.
set -Eeuo pipefail

ENV_FILE="${NODE_C_RESTIC_ENV:-/etc/node-c-restic/env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

load_env() {
    if [[ -f "$ENV_FILE" ]]; then
        # shellcheck disable=SC1090
        set -a
        source "$ENV_FILE"
        set +a
    elif [[ -f "$SCRIPT_DIR/node-c-client.env.example" ]]; then
        echo "warning: using example env (not $ENV_FILE)" >&2
        # shellcheck disable=SC1091
        set -a
        source "$SCRIPT_DIR/node-c-client.env.example"
        set +a
    else
        echo "env file not found: $ENV_FILE" >&2
        exit 2
    fi
}

dry_run() {
    [[ "${RESTIC_DRY_RUN:-1}" != "0" ]]
}

require_vars() {
    : "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY required}"
    : "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE required}"
    if [[ ! -f "$RESTIC_PASSWORD_FILE" ]] && dry_run; then
        echo "dry-run: password file missing ($RESTIC_PASSWORD_FILE); skipping restic calls" >&2
        return 1
    fi
    [[ -f "$RESTIC_PASSWORD_FILE" ]] || {
        echo "password file missing: $RESTIC_PASSWORD_FILE" >&2
        exit 2
    }
    if [[ "$RESTIC_REPOSITORY" != *"/node-c" ]]; then
        echo "RESTIC_REPOSITORY must end with /node-c (private repo user)" >&2
        exit 2
    fi
    return 0
}

run_restic() {
    if dry_run; then
        echo "dry-run: restic --password-file *** $*"
        return 0
    fi
    restic --password-file "$RESTIC_PASSWORD_FILE" "$@"
}

main() {
    load_env
    if ! require_vars; then
        echo "onboarding preview complete (dry-run, no password file)."
        exit 0
    fi
    if ! command -v restic >/dev/null 2>&1; then
        echo "install restic before onboarding (apt/brew/restic release tarball)" >&2
        exit 2
    fi
    echo "node-c restic onboarding against $RESTIC_REPOSITORY"
    if run_restic snapshots >/dev/null 2>&1; then
        echo "repository already initialized"
    else
        run_restic init
    fi
    BACKUP_PATHS="${BACKUP_PATHS:-$HOME/actions-runners}"
    # shellcheck disable=SC2086
    run_restic backup --json $BACKUP_PATHS
    if dry_run; then
        echo "dry-run complete; set RESTIC_DRY_RUN=0 in $ENV_FILE to apply."
    fi
}

main "$@"
