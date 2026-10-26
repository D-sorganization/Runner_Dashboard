# Escalation lane (Refs #2952)

Rare **priority:expedite** work skips the self-hosted runner queue and (after slice 3) can jump the merge queue. Required checks and branch protection are unchanged.

## Apply the expedite label

Trusted GitHub actors: `dieterolson` and `d-sorggeneralagent[bot]` (fleet GitHub App). Anyone else gets the label removed by `priority-expedite-guard`.

**Dieter** — use personal GitHub CLI on the box:

```bash
gh pr edit <PR> --repo D-sorganization/<Repo> --add-label "priority:expedite"
```

**Barb (agent-grok)** — use the machine’s normal `gh` command. On fleet nodes that is already the **fleet App wrapper** (`d-sorggeneralagent[bot]`). Do **not** set `GH_TOKEN` from Runner Dashboard bearer files (`agent-tokens/*.token`); those authenticate to the hub API, not GitHub.

```bash
gh pr edit <PR> --repo D-sorganization/<Repo> --add-label "priority:expedite"
```

Fleet-wide label bootstrap (operators):

```bash
python3 scripts/ensure_expedite_label.py
```

## Hub audit

With org secrets `RUNNER_DASHBOARD_URL` and `HUB_FLEET_TOKEN`, the guard workflow posts to `POST /api/fleet/escalation-events`. Operators list events:

```bash
curl -sS -H "Authorization: Bearer $RUNNER_DASHBOARD_TOKEN" \
  "$RUNNER_DASHBOARD_URL/api/staff/escalations"
```

Use an operator or staff bearer from the hub identity store — not a GitHub token.
