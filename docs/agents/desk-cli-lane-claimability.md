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
| #3700 George | `judgement:design` + `blocked` until Barb/Dieter confirm schedule/duty—spec on issue body + `docs/agents/issue-specs/3700-george-persona-port.md` |
| #3702 Linus | `judgement:design` + `blocked` until Dieter scope ruling—`3702-linus-persona-port.md` |
| #3704 | **Closed** checklist epic—not pickable (`state != open`); pick #3792 or newly labeled CLI tasks |

## Why agy / rd-barb can still show “nothing claimable” (2026-11-02)

1. **`pickable` false:** `judgement:design` / `judgement:contested` (inventory rule in `issue_inventory.is_pickable`).
2. **Hold labels:** `blocked`, `do-not-automate`, `do-not-merge` exclude `/api/coordination/jobs` even when inventory pickable is true.
3. **Missing taxonomy:** open issues with **zero labels** fail lane filters that require `tier:cli`, `complexity:*`, etc.
4. **Thin CLI queue:** on 2026-11-02 only **#3792** matched `tier:cli` + `judgement:objective` + open + unclaimed on RD; label more fix/tasks via `scripts/rd_unlabeled_issue_labels.json` + `scripts/apply_rd_issue_labels.sh`.
5. **Lane-specific filters:** remediation-style lanes often enable pickable-only plus repo/tier/complexity filters in the UI or local wrapper—match labels to the lane profile.

## Unlabeled open issues (23 on 2026-11-02)

Missing `type:*`, `complexity:*`, `effort:*`, `judgement:*` per `docs/issue-taxonomy.md`. Until labeled, orchestrators cannot route “three routine objective CLI tasks” reliably.

Apply labels with `scripts/apply_rd_issue_labels.sh` (requires `gh` issue write on a fleet bot host). If the cloud/bot token lacks `issues:write`, run `scripts/sync_issue_specs_to_github.sh` on the Desk hub with **`gh-personal`** (owner login).

## Holds respected by job board

Schedule holds from `staff/holds` (RD #2657 deploy hold, Sidekick packaging, Assessment Board gate, etc.) exclude issues carrying matching `hold:<id>` labels. GM #5478, AD #5158, RD #2658/#2880—verify labels on affected issues before dispatch.

## Presence / claims

Before coding: claim on Repository_Management **#1576** presence board (not accessible from this cloud agent token) and `GET /api/coordination/claims?repo=Runner_Dashboard&issue=N` on the hub. This session registers intent via PR + `Closes #N` instead.
