# Release target qualification (#3596)

Read-only preflight before upgrading the Runner Dashboard on a **dashboard worker node** (for example `node-b` in fleet docs). Does **not** deploy, change credentials, relabel runners, or bypass branch protection.

## When to run

After a release candidate is published through normal CI/publication gates and **before** applying a Brick-only install. Coordinate with Dots Barb on Repository_Management **#2539** before any production edit.

## Command

```bash
export FLEET_API_URL="http://hub-a:8321"
export FLEET_API_TOKEN="$(cat ~/.config/runner-dashboard/agent-tokens/agent-codex.token)"
python3 scripts/release_target_qualify.py
```

Exit `0` when all non-deferred checks pass. The `live_codex_worker_task` row is **deferred validation**: complete it manually after deploy by running one dashboard-managed Codex CLI task on the target node and capturing receipts.

## Checks

| Check | Meaning |
| ----- | ------- |
| `hub_health` | `/api/health` reachable |
| `fleet_status` | `/api/fleet/status` returns deployed SHA metadata |
| `coordination_jobs` | Job board readable for `Runner_Dashboard` |
| `codex_chat_argv` | Local contract: `exec resume` + `--json` (#3595) |
| `live_codex_worker_task` | Deferred until post-deploy worker proof |

Rollback stays the supported backup/install path on the target node documented in deploy runbooks; this script never mutates the host.
