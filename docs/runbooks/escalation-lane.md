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

## Runner lane (slice 2)

When a PR carries `priority:expedite`, `ci-standard` routes `pull_request` and `merge_group` jobs to `ubuntu-latest` via the `pick-runner` output (private repos included). `workflow_dispatch` with `force_local` still wins.

Concurrent expedites per repository default to **3**. Override:

```bash
gh variable set EXPEDITE_CONCURRENT_CAP --repo D-sorganization/Runner_Dashboard --body 3
```

Over the cap, the guard workflow refuses the label and posts the hub message on the PR.

## Merge queue jump (slice 3)

`POST /api/staff/escalate` with body `{"repo","pr","reason"}` (requires `staff.admin`). Uses GraphQL `enqueuePullRequest(jump: true)`. **Jumping rebuilds in-flight merge-queue entries.**

The hub verifies `mergeQueueEntry.position` (and queue depth when available) after the mutation. GitHub can return success while leaving the PR at the back of the queue (for example position 27 of 27). In that case the API responds with `status: jump_ignored` and a clear message — do not treat a bare GraphQL success as a jump.

**Ruleset bypass:** effective jumping requires a **ruleset bypass actor** with merge-queue permission on the repository ruleset (Runner_Dashboard ruleset `24444089`). Personal `gh` login alone is not enough when no bypass actor covers that login. Whether to add a bypass actor is an owner decision; this runbook does not change rulesets.

CLI (operator hub bearer — not a GitHub token):

```bash
export RUNNER_DASHBOARD_URL="https://<hub>:8321"
export RUNNER_DASHBOARD_TOKEN="<operator bearer>"
python3 scripts/escalate.py --repo D-sorganization/Runner_Dashboard --pr <N> --reason "why"
```

When the fleet `gh` token lacks merge-queue jump permission, the hub returns `gh_personal_command` for Dieter or Barb to run locally (never copy tokens into env files). If the command succeeds but position stays at the tail, use `jump_ignored` guidance above — fix bypass actors, not tokens.
