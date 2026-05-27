# GitHub App Auth Runbook

## Purpose

The dashboard should use GitHub App installation tokens for GitHub API polling.
Installation tokens have a separate rate-limit bucket from the operator's
`gh`/PAT credentials, which prevents dashboard refreshes from exhausting the
same token used by automation and emergency maintenance.

`GH_TOKEN` remains supported as a fallback during rollout, but it should not be
the primary production credential.

## Required GitHub App Permissions

Configure the existing GitHub App installation on `D-sorganization` with the
minimum permissions needed by the dashboard features in use:

| Permission     | Access         | Used for                              |
| -------------- | -------------- | ------------------------------------- |
| Actions        | Read and write | List, cancel, and rerun workflow runs |
| Administration | Read           | List self-hosted org runners          |
| Contents       | Read           | Repository inventory and metadata     |
| Metadata       | Read           | Required by GitHub                    |
| Pull requests  | Read           | PR and queue context                  |

If a dashboard feature returns 403 after rollout, add the narrow permission for
that feature and reinstall/update the App installation.

## Deploy Credentials On A WSL Dashboard Node

Use the GitHub App's downloaded private key PEM. Do not paste this key into the
repository.

```bash
install -d -m 700 ~/.config/runner-dashboard
install -m 600 /mnt/c/Users/diete/Downloads/d-sorganization-runner-dashboard.private-key.pem \
  ~/.config/runner-dashboard/github-app-private-key.pem

python3 - <<'PY'
from pathlib import Path

env = Path.home() / ".config/runner-dashboard/env"
lines = env.read_text().splitlines() if env.exists() else []
updates = {
    "GITHUB_APP_ID": "REPLACE_WITH_NUMERIC_APP_ID",
    "GITHUB_APP_INSTALLATION_ID": "REPLACE_WITH_INSTALLATION_ID",
    "GITHUB_APP_PRIVATE_KEY_FILE": str(Path.home() / ".config/runner-dashboard/github-app-private-key.pem"),
}
kept = [line for line in lines if line.split("=", 1)[0] not in updates]
env.write_text("\n".join(kept + [f"{k}={v}" for k, v in updates.items()]) + "\n")
env.chmod(0o600)
PY

sudo systemctl restart runner-dashboard.service
```

Repeat this on each WSL dashboard instance that calls GitHub directly, including
`WSL` / ControlTower-NVMe and `ControlTower-SSD`.

## Verify

```bash
systemctl show runner-dashboard.service -p EnvironmentFiles --no-pager
curl -fsS http://127.0.0.1:8321/api/health | python3 -m json.tool
journalctl -u runner-dashboard.service --since "5 min ago" --no-pager \
  | grep -Ei "GitHub App|auth_source|rate limit|auth_error" || true
```

The health endpoint should report `github_api: connected` once the service has
successfully exchanged the App JWT for an installation token.

## Rollback

Remove or comment the three `GITHUB_APP_*` lines from
`~/.config/runner-dashboard/env` and restart the service. The dashboard will
fall back to `GH_TOKEN` / `GITHUB_TOKEN` when present.
