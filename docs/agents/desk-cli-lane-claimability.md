# Desk CLI lanes: why issues show “nothing claimable”

Investigation for idle Desk CLI lanes (2026-11-02). Hub APIs and taxonomy docs on `main`.

## Sprint brief / coordination

- **`GET /api/coordination/briefing`**: priorities, holds, staff runs, claim protocol hint—not a work queue filter by itself (`backend/coordination/briefing.py`).
- **`GET /api/v1/fleet/brief`**: cached mash-up of briefing + merge queue + hub status + issue/PR inventory (`backend/coordination/brief.py`). Issues come from `issue_inventory.fetch_all_issues`; **no extra “claimable” filter** beyond inventory shape.
- **`GET /api/coordination/jobs`**: explicit **claimable job board**—open, unclaimed, no `claim:*`, no hold labels (`blocked`, `do-not-automate`, `do-not-merge`), no `code-request:board_review`, no schedule `hold:*` from `staff/holds`, no closing PR, lease check available (`backend/coordination/jobs.py`, `job_filters.py`).

Desk CLI lanes that mirror **remediation dispatch** use **`pickable`** from issue inventory (`backend/issue_inventory.py`):

1. `state == open`
2. No linked open PR
3. No active `claim:*` label
4. `judgement` not in `design` or `contested`

Lanes also typically filter on **taxonomy** (complexity, tier, repo). Issues **without labels** parse empty taxonomy and may fail lane-specific filters even when `pickable` is true.

## Spec gaps blocking persona lanes

| Issue | Blocker |
| ----- | ------- |
| #3700 George | No confirmed Grok duty/schedule in RM/RD docs—see `docs/agents/issue-specs/3700-george-persona-port.md` |
| #3702 Linus | Dieter scope decision per `role-seats.md`—see `3702-linus-persona-port.md` |
| #3704 | Closed tracker; code on main—pick fix/tasks, not the epic checkbox |

## Unlabeled open issues (23 on 2026-11-02)

Missing `type:*`, `complexity:*`, `effort:*`, `judgement:*` per `docs/issue-taxonomy.md`. Until labeled, orchestrators cannot route “three routine objective CLI tasks” reliably.

Apply labels with `scripts/apply_rd_issue_labels.sh` (requires `gh` issue write on a fleet bot host).

## Holds respected by job board

Schedule holds from `staff/holds` (RD #2657 deploy hold, Sidekick packaging, Assessment Board gate, etc.) exclude issues carrying matching `hold:<id>` labels. GM #5478, AD #5158, RD #2658/#2880—verify labels on affected issues before dispatch.

## Presence / claims

Before coding: claim on Repository_Management **#1576** presence board (not accessible from this cloud agent token) and `GET /api/coordination/claims?repo=Runner_Dashboard&issue=N` on the hub. This session registers intent via PR + `Closes #N` instead.
