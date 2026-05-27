# GitHub App Auth Runbook

## Purpose

The dashboard should use GitHub App installation tokens for GitHub API polling.
Installation tokens have a separate rate-limit bucket from the operator's
`gh`/PAT credentials, which prevents dashboard refreshes from exhausting the
same token used by automation and emergency maintenance.

`GH_TOKEN` remains supported as a fallback during rollout, but it should not be
the primary production credential. A healthy production dashboard should report
`github_auth_source: github_app` from `/api/health`.

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

## Runtime Contract

The dashboard service reads credentials from the WSL runtime environment. It
does not use the Windows `gh` keyring or the Codex scheduled-task runtime
directly.

Use these variables in `~/.config/runner-dashboard/env`:

| Variable                      | Required | Purpose                                           |
| ----------------------------- | -------- | ------------------------------------------------- |
| `GITHUB_APP_ID`               | Yes      | Numeric GitHub App ID                             |
| `GITHUB_APP_INSTALLATION_ID`  | Yes      | Numeric org installation ID                       |
| `GITHUB_APP_PRIVATE_KEY_FILE` | Yes      | Path to the WSL-local PEM file                    |
| `GITHUB_APP_PRIVATE_KEY`      | No       | Inline PEM fallback; avoid for normal deployments |
| `GH_TOKEN` / `GITHUB_TOKEN`   | No       | Emergency fallback only                           |

Older `GH_APP_*` names are legacy and should not be used for new deployments.

## Secret Storage Rules

Keep the downloaded GitHub App PEM outside the repository. On ControlTower, the
host-level Codex setup may keep the source PEM under
`C:\ProgramData\CodexGithubApp\codex-github-app.pem` with restricted ACLs. That
host path is a source of truth for Codex scheduled-task auth and can be used to
copy the key into WSL, but the dashboard systemd service should read a WSL-local
copy.

For each WSL dashboard distro:

- PEM file: `~/.config/runner-dashboard/github-app.private-key.pem`
- env file: `~/.config/runner-dashboard/env`
- directory mode: `0700`
- file mode: `0600`

Do not put the PEM or installation token in:

- checked-in repo files
- `~/actions-runners/dashboard/`
- systemd unit files
- shell history, screenshots, logs, PR text, issues, or memory notes

## Deploy Credentials On A WSL Dashboard Node

Use the GitHub App's downloaded private key PEM. Do not paste this key into the
repository.

```bash
install -d -m 700 ~/.config/runner-dashboard
install -m 600 /mnt/c/ProgramData/CodexGithubApp/codex-github-app.pem \
  ~/.config/runner-dashboard/github-app.private-key.pem

python3 - <<'PY'
from pathlib import Path

env = Path.home() / ".config/runner-dashboard/env"
lines = env.read_text().splitlines() if env.exists() else []
updates = {
    "GITHUB_APP_ID": "REPLACE_WITH_NUMERIC_APP_ID",
    "GITHUB_APP_INSTALLATION_ID": "REPLACE_WITH_INSTALLATION_ID",
    "GITHUB_APP_PRIVATE_KEY_FILE": str(Path.home() / ".config/runner-dashboard/github-app.private-key.pem"),
}
kept = [line for line in lines if line.split("=", 1)[0] not in updates]
env.write_text("\n".join(kept + [f"{k}={v}" for k, v in updates.items()]) + "\n")
env.chmod(0o600)
PY

sudo systemctl restart runner-dashboard.service
```

Repeat this on each WSL dashboard instance that calls GitHub directly, including
`WSL` / ControlTower-NVMe and `ControlTower-SSD`.

### ControlTower dual-distro pattern

ControlTower runs two dashboard-backed WSL environments. Configure both with the
same GitHub App installation, but keep each distro's env file and PEM copy local
to that distro.

```powershell
wsl -d WSL -- bash -lc 'install -d -m 700 ~/.config/runner-dashboard && install -m 600 /mnt/c/ProgramData/CodexGithubApp/codex-github-app.pem ~/.config/runner-dashboard/github-app.private-key.pem'
wsl -d ControlTower-SSD -- bash -lc 'install -d -m 700 ~/.config/runner-dashboard && install -m 600 /mnt/c/ProgramData/CodexGithubApp/codex-github-app.pem ~/.config/runner-dashboard/github-app.private-key.pem'
```

Then set `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, and
`GITHUB_APP_PRIVATE_KEY_FILE` inside each distro's
`~/.config/runner-dashboard/env`, restart `runner-dashboard.service`, and verify
the correct dashboard port:

```powershell
wsl -d WSL -- bash -lc 'sudo systemctl restart runner-dashboard.service && curl -fsS http://127.0.0.1:8321/api/health'
wsl -d ControlTower-SSD -- bash -lc 'sudo systemctl restart runner-dashboard.service && curl -fsS http://127.0.0.1:8322/api/health'
```

## Verify

```bash
systemctl show runner-dashboard.service -p EnvironmentFiles --no-pager
curl -fsS http://127.0.0.1:8321/api/health | python3 -m json.tool
journalctl -u runner-dashboard.service --since "5 min ago" --no-pager \
  | grep -Ei "GitHub App|auth_source|rate limit|auth_error" || true
```

The health endpoint should report `github_auth_source: github_app`,
`github_auth_status: ok`, and `github_api: connected` once the service has
successfully exchanged the App JWT for an installation token. Recent logs should
also show a successful `POST /app/installations/<id>/access_tokens` exchange.

## Rollback

Remove or comment the three `GITHUB_APP_*` lines from
`~/.config/runner-dashboard/env` and restart the service. The dashboard will
fall back to `GH_TOKEN` / `GITHUB_TOKEN` when present.
