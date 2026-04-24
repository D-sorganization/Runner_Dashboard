#!/usr/bin/env bash
# ==============================================================================
# refresh-token.sh — Called by systemd ExecStartPre before the dashboard starts.
#
# Reads the current gh CLI token and writes it to the secrets EnvironmentFile.
# This means re-authenticating with 'gh auth login' is all that's needed when
# a token expires — no manual service file editing required.
#
# The token comes from gh's stored credentials (~/.config/gh/hosts.yml),
# which are managed by Git Credential Manager (GCM) bridging to Windows
# Credential Manager on WSL2 machines.
# ==============================================================================

set -euo pipefail

SECRETS_FILE="${HOME}/.config/runner-dashboard/env"
mkdir -p "$(dirname "${SECRETS_FILE}")"
touch "${SECRETS_FILE}"
chmod 600 "${SECRETS_FILE}"

TOKEN=$(gh auth token 2>/dev/null || echo "")

# Older gh (<2.40) prints its usage/error to stdout, so `2>/dev/null` doesn't
# silence it. Treat anything that doesn't look like a GitHub token
# (gho_/ghp_/ghu_/ghs_/ghr_) as empty so the fallback kicks in.
if [[ ! "${TOKEN}" =~ ^(gho_|ghp_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{30,}$ ]]; then
    TOKEN=""
fi

# Fallback for older gh CLI (<2.40): read directly from ~/.config/gh/hosts.yml
if [[ -z "${TOKEN}" ]]; then
    TOKEN=$(python3 -c "
import re, pathlib, sys
hosts = pathlib.Path.home() / '.config/gh/hosts.yml'
if not hosts.exists(): sys.exit(0)
m = re.search(r'oauth_token:\s*(\S+)', hosts.read_text())
print(m.group(1) if m else '', end='')
" 2>/dev/null || echo "")
fi

if [[ ! "${TOKEN}" =~ ^(gho_|ghp_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{30,}$ ]]; then
    TOKEN=""
fi

if [[ -z "${TOKEN}" ]]; then
    echo "[refresh-token] WARNING: gh auth token returned empty and hosts.yml parse failed — dashboard will have limited GitHub API access" >&2
    echo "[refresh-token] Fix: run 'gh auth login && gh auth refresh -s admin:org' then restart the service" >&2
    exit 0
fi

# Update GH_TOKEN line, preserve everything else (FLEET_NODES etc.)
sed -i '/^GH_TOKEN=/d' "${SECRETS_FILE}"
printf 'GH_TOKEN=%s\n' "${TOKEN}" >> "${SECRETS_FILE}"

echo "[refresh-token] GH_TOKEN refreshed from gh credentials" >&2
