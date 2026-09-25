# Agent Organization: Runner Dashboard Implementation Plan

- **Date:** 2026-09-25
- **Status:** Phase 0 was approved and is **done**: WP-0.1 #1474 merged as #1481, WP-0.2 #1475 merged as #1482. **Phase 1 approved on 2026-09-25** and filed as #1516 (WP-1.1), #1517 (WP-1.2) and #1518 (WP-1.3), with Repository_Management#1781 and #1782. Phase 2 is still proposed.
- **Governing issue:** #1463 (Runner_Dashboard half of [Repository_Management#1766](https://github.com/D-sorganization/Repository_Management/issues/1766))
- **Canonical analysis:** `Repository_Management/docs/plans/2026-09-25_AGENT_ORG_GAP_ANALYSIS.md` ([RM PR #1767](https://github.com/D-sorganization/Repository_Management/pull/1767)). This file does not repeat the analysis. It is the Runner_Dashboard half: the specific seams, work packages and acceptance criteria that implement the analysis's path forward in this repository.
- **Evidence baseline:** Runner_Dashboard `7f73b08`, rechecked against `main` at `9bbeec6` (2026-09-25 19:05 UTC). Since the baseline, CR-7 Board Proposals (#1444), CR-6 board routing gate (#1469) and SC-C7 routing evaluation (#1442, issue #1340) have merged. The three dangling role names and the inbox stub are still present on `main`.

## 1. Why This Repository Carries Most of the Work

Staff roles are defined in Repository_Management (`staff/roles/*.yml`) and executed here. Four of the five gaps in the analysis are therefore Runner_Dashboard gaps:

| Gap                                         | Where it lives here                                                                                     |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| No quality gate on staff output             | `backend/staff/classifier.py:363`, `backend/staff/action_executors.py:48-56`                            |
| Broken routing to review and planning roles | `backend/staff/action_executors.py:66`, `:185`, `:207`                                                  |
| Board proposals invisible to the owner      | `backend/staff/inbox.py:347`                                                                            |
| Vision-to-code pipeline incomplete          | `backend/code_requests/` (CR-6 and CR-7 merged; CR-4, CR-5 and CR-8 unbuilt; approval flags unenforced) |
| No outcome metrics                          | Next to `backend/routers/staff_usage.py` (cost only today)                                              |

The role-file changes (new `code-reviewer`, `product-owner`, `chief-architect`) land in Repository_Management. Everything that makes those roles run and be checked lands here.

## 2. Principles for These Changes

1. **Deterministic checks are code, not roles.** The gate and the metrics are pure functions over the run store and GitHub; they get unit tests and never an LLM.
2. **Additive, then enforced.** Each gate first ships in `report` mode (it records a verdict, changes no status), then moves to `enforce` behind a setting once a week of data shows a low false-positive rate. This keeps the change reversible, per this repo's "Reversible" principle.
3. **One seam per concern.** The classifier keeps classifying process exit. Verification is a separate post-run step, so a GitHub outage can never make a finished run look failed (the tabs stay orthogonal).
4. **No new cross-repo imports.** Role names resolve against the loaded roster from `backend/staff/roles.py`, never against hard-coded strings.

## 3. Work Packages

Estimates are rough. The **Tier** column follows `Repository_Management/docs/agents/AGENT_TIER_ROUTING.md`.

### Phase 0: Fix the Pipes

**WP-0.1: Resolve role names against the roster** · Tier: `cli` · Size: S

- **Change:** replace the literals `"critic"` (`action_executors.py:66`), `"lead_architect"` (`:185`) and `"board_secretary"` (`:207`) with constants validated against `roles.load_roles()` at import or registration time:
  - reviewer → `fleet-critic` until `code-reviewer` exists;
  - code requests → `barb` until `chief-architect` exists;
  - proposals → `board-secretary`.
- **Test first:** a unit test that walks every `ActionDefinition` and every default role string in `action_executors.py`, and asserts each resolves to a dispatchable role in the loaded roster. It fails today on all three.
- **Done when:** the test passes. Dispatching `staff.review_pr` without a `reviewer` starts a `fleet-critic` run instead of failing role lookup.

**WP-0.2: Board proposals in the owner inbox** · Tier: `cli` · Size: S–M

- **Change:** implement the `board_proposals` source in `backend/staff/inbox.py` over the CR-7 store that merged in #1444. Do not make a second GitHub read.
  - Call `backend/proposals/store.list_github_proposals(state="open")`, or its wrapper in `backend/proposals/service.py`, cached with the inbox TTL.
  - Map each proposal awaiting a decision to an `InboxItem` with kind `board_proposal`, its age, and a link to the Fleet Command Proposals tab.
  - Keep the existing try/except, so failure degrades to `status="unavailable"`.
- **Test first:** mock the GitHub response. Assert that the items and counts appear, and that a GitHub error yields `unavailable`, not a 5xx.
- **Done when:** proposals filed through the RM `board-proposal` form appear under "Waiting on you".

### Phase 1: Verify Before Trusting

**WP-1.1: Run verification step** · Tier: `strong` (it touches the run lifecycle) · Size: M

- **Seam:** a new `backend/staff/verification.py`, called after `classify_execution_result` from the runner's finish path and from `reconcile.py` for runs that finish while the dashboard is down.
- **Record:** add three fields to `StaffRunRecord`: `verification` (`unverified | verified | failed | not_applicable`), `verification_detail`, and `pr_number` (int or null). This follows the existing additive-column pattern in `store.py:156`.
- **Rules:**
  - Find the PR by the run's `branch` (`staff/<role>-<issue>-<hex>`) in the run's `repo`.
  - Roles whose permissions have `open_pr: false` are `not_applicable`.
  - For a run that claims success, `verified` requires an open or merged PR whose head CI is not failing.
  - `failed` means no PR, or CI red on the head.
  - `pending` CI stays `unverified`, and `reconcile` rechecks it later.
- **Modes:** `STAFF_VERIFY_MODE=report` (the default) only records. `enforce` downgrades a `succeeded` run with a `failed` verification to `failed`, with `failure_class=unverified_output`.
- **Also:** `verify_staff_dispatch` (`action_executors.py:48`) reports the run's `verification`, not just that the run record exists.
- **Tests:** a table-driven unit test over (claimed status, permissions, PR state, CI state) to verdict. Also a reconcile test for a run that finished offline.
- **Done when:** a week of report-mode data exists, visible in the run detail page and in the outcomes API.

**WP-1.2: `GET /api/staff/outcomes`** · Tier: `cli` · Size: M

- **Seam:** a new router beside `routers/staff_usage.py`, with the same query shape (`since`, `group_by=role|provider|repo`).
- **Data:**
  - From the run store: runs, succeeded, verified, cost.
  - From GitHub, only for PRs the run store already knows about (no fleet sweep, per the network-hygiene rules): opened, merged, closed unmerged, CI first-pass, and "fix within 48 hours". That last one means a later merged commit whose message or linked issue references the same issue.
- **Derived:** merge rate, verified rate, and cost per merged PR.
- **Tests:** a pure aggregation function tested on fixtures; the router is tested with the store mocked.
- **Done when:** the endpoint serves the scorecard in §7 of the analysis, and the Staff page shows a compact table.

**WP-1.3: Code-reviewer runtime support** · Tier: `strong` · Size: M

- **Depends on:** the RM role file `staff/roles/code-reviewer.yml` (RM side).
- **Change:**
  - `staff.review_pr` defaults to `code-reviewer` once it is loaded.
  - The dispatcher picks the reviewer's provider to _differ_ from the provider that authored the PR, using the run store's `provider` for that branch.
  - The reviewer emits a structured verdict line, `STAFF_RESULT: review approve|changes|escalate #<pr>`, which is parsed into `outcome` the way `consolidation.py` parses its own `STAFF_RESULT` line.
- **Modes:** advisory. It posts a review comment only; a "request changes" review is not used until the owner turns blocking on.
- **Tests:** provider-selection unit tests, including when only one provider is available (then a different model on the same provider, and the verdict is marked `same-provider`); verdict parsing tests.

### Phase 2: Vision to Code (Code Requests)

These already have issues under epic #1279. This plan only adds the role mapping and the enforcement points.

| Work package                              | Existing issue            | Role binding                                                                              | Enforcement added                                                                                               |
| ----------------------------------------- | ------------------------- | ----------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| WP-2.1 Planner (CR-4)                     | #1285                     | `chief-architect` (planner profile)                                                       | `plan_requires_approval` blocks `planning → planned` until an owner approval action exists                      |
| WP-2.2 Executor routing and rollup (CR-5) | #1287                     | tier agents via the existing dispatch envelope; the "dispatcher" is this code, not a role | Each child issue carries acceptance criteria copied from the plan                                               |
| WP-2.3 Board gate (CR-6)                  | #1286 (**merged**, #1469) | `board-secretary`                                                                         | Already built. The remaining step is to bind the reviewing role by roster name (WP-0.1 pattern)                 |
| WP-2.4 Proposals API/tab (CR-7)           | #1284 (**merged**, #1444) | `board-secretary`                                                                         | Already built; WP-0.2 surfaces it in the owner inbox                                                            |
| WP-2.5 Acceptance (CR-8)                  | #1288                     | `qa-verifier`, or scripted checks                                                         | `executing → done` requires every child PR verified (WP-1.1) and the acceptance criteria checked                |
| WP-2.6 Decision SLA                       | new                       | `barb`                                                                                    | Inbox items gain `decide_by` and `default_if_silent`; Barb's follow-up sweep applies the default and records it |

The `product-owner` role writes the PRD section of a Code Request during `draft → triage`. Its only runtime need here is a `code_request.update` action that is limited to the description field.

## 4. Ordering and Dependencies

```
WP-0.1 ─┐
WP-0.2 ─┼─► WP-1.1 ─► WP-1.2 ─► (owner reviews baseline) ─► WP-1.3 advisory ─► Phase 2
        │                                                      ▲
        └──────────── RM: code-reviewer.yml ───────────────────┘
```

- WP-0.1 and WP-0.2 are independent and can run in parallel as `tier:cli` dispatches.
- WP-1.2 depends on WP-1.1 because it reads `verification` and `pr_number`.
- Phase 2 starts only after WP-1.1 has run in report mode for a week.

## 5. Collision Check Against In-Flight Work

These are the live leases seen on the presence board at 2026-09-25 18:26 UTC.

| Session                             | Issue / paths                                                                                        | Overlap with this plan                                                                                                                                               |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `claude-deskcomputer-20260925`      | #1340 (`backend/staff/routing_eval.py`, `frontend/src/pages/Staff/Board.tsx`); consolidated PR #1458 | #1340 merged (#1442), so the WP-0.1 collision is cleared. WP-0.1 should add its roster-resolution check next to the routing evaluation set rather than duplicate it. |
| `claude-oglaptop-20260925-tracking` | #1345 shell (merged, #1449); now SC-G6 #1338 (retire Cline Launcher, `/api/agent-launcher`)          | None.                                                                                                                                                                |
| CR-1 #1281, CR-2 #1282 (in review)  | `backend/code_requests/*`                                                                            | Phase 2 must rebase on them; no Phase 0/1 package touches `backend/code_requests/`.                                                                                  |

## 6. Open Questions Sent to the Active Sessions

Sent on 2026-09-25 to `claude-deskcomputer-20260925` and `claude-oglaptop-20260925-tracking`. **Delivery failed.** The cloud session's GitHub proxy appends an attribution footer to every comment it posts, and `shared_scripts/agent_messages.parse` rejects any envelope that does not end in the closing code fence. The messages show up as "Malformed coordination comment" and never reached either inbox. The questions are now on #1463 for the owner to relay, or for either session to answer there. Replies will be folded in here.

1. Collisions with unmerged work beyond those in §5.
2. The verification seam: a post-run step (proposed) or inside the classifier.
3. Outcomes from the run store plus targeted GitHub reads (proposed), or GitHub only.
4. Code reviewer as a staff role (proposed) or as a Code Request profile.
5. The owner and order of each work package.

## 7. What Is Explicitly Out of Scope Here

- No new roles are defined in this repository. Role files belong in Repository_Management.
- No change to `merge` permissions. The standing green-PR auto-merge policy is unchanged.
- No fleet-wide GitHub sweeps. Every GitHub read is scoped to a PR or issue the run store or inbox already references.
