# Kanban board projection (state rules)

Epic #4317, slice 1 #4318. Expert panel `th_8b59b45260c9` (unanimous, 2026-11-10).

The kanban board is a **read-only projection**. Cards are computed from upstream
sources; the board stores no card state. Slice 1 defines the column mapper and
its contracts. GitHub ingestion (#4319), claims/leases (#4320), holds (#4321),
and UI (#4322) are out of scope here.

Implementation: `backend/kanban_projection.py`. GitHub ingestion (#4319):
`backend/kanban_github_ingestion.py` and [kanban-github-ingestion.md](kanban-github-ingestion.md).

## Columns

| Column id | Display name | Meaning |
| --- | --- | --- |
| `merged` | Merged | Work landed: linked PR is `merged`, or the issue is closed with merge evidence. |
| `waiting_on_dieter` | Waiting on Dieter | Owner-only gate: schedule holds, `hold:*` labels, or an explicit owner-wait signal. Read-only on the board. |
| `armed` | Armed | Open, non-draft PR with auto-merge enabled (merge queue counts as armed when `in_merge_queue` is true). |
| `review` | Review | Open, non-draft PR still needing human review at the **current head**. |
| `pr_open` | PR open | Open PR that is not armed and not in the review state. Draft PRs map here. |
| `claimed` | Claimed | Open issue with an active coordination lease and no open PR. |
| `issue_open` | Issue open | Open, unclaimed issue with no open PR. |

`unknown` is **not** a column. When required evidence is missing or marked
degraded, the card's `column` is `unknown` and `column_label` is `Unknown`.
The projector never infers a lifecycle stage without evidence.

## Inputs (snapshot)

Ingestion (#4319–#4321) assembles a `KanbanWorkItemSnapshot` per card:

| Slice | Fields | Source (future) |
| --- | --- | --- |
| Issue | `KanbanIssueSnapshot` | GitHub issue |
| Pull request | `KanbanPullRequestSnapshot` | GitHub PR linked to the issue |
| Checks | `KanbanChecksSnapshot` | GitHub check runs on PR head |
| Reviews | `KanbanReviewsSnapshot` | GitHub review events on PR |
| Auto-merge | `KanbanAutoMergeSnapshot` | GitHub `auto_merge` / merge queue |
| Claim / lease | `KanbanClaimSnapshot` | `kanban_claims_ingestion.claim_snapshot_from_check` ← `coordination.claims.check` (read-only) |
| Owner wait | `KanbanOwnerWaitSnapshot` | Holds list, issue labels, staff approvals (`backend/kanban_owner_wait.py`, #4321) |
| Staff run | `KanbanStaffRunSnapshot` | `kanban_claims_ingestion.staff_run_snapshot_for_issue` ← `staff.store.RunStore` (read-only) |

Each sub-record carries `available`, optional `degraded`, and URLs for source
links. Slice 1 accepts these structures as plain data; it performs no I/O.

## Precedence

Several predicates can match one snapshot (for example an armed PR still has
open checks). The card sits in the **single** column with the highest
precedence below (top wins):

1. `merged`
2. `waiting_on_dieter`
3. `armed`
4. `review`
5. `pr_open`
6. `claimed`
7. `issue_open`

`unknown` is evaluated **before** precedence: if the snapshot fails the
fail-closed rules in the next section, precedence is not applied.

## Fail-closed (`unknown`)

| Condition | Result |
| --- | --- |
| Issue snapshot unavailable or `degraded` | `unknown` |
| Issue `state` is `closed` and neither merged PR evidence nor explicit merged mapping | `unknown` |
| Open issue, no open PR, and claim snapshot unavailable or `degraded` | `unknown` (cannot distinguish Issue open vs Claimed) |
| Linked PR `state=open` but snapshot `available=false` or `degraded` (even when claim snapshot is healthy) | `unknown` — do not fall through to Issue open / Claimed |
| Open non-draft PR and reviews snapshot unavailable or `degraded` when `review_requested` or `changes_requested_at_head` is true | `unknown` |
| Open non-draft PR and both PR and review snapshots carry conflicting `head_sha` | `unknown` |
| Auto-merge snapshot unavailable or `degraded` on an open non-draft PR **unless** `in_merge_queue` is true (queue state is taken from the PR snapshot) | `unknown` |
| Draft open PR | auto-merge and review evidence **not** required (card maps to `pr_open`) |

When `unknown`, `reasons` lists every fail-closed trigger. No column guess is
made.

## Predicate reference (open issue unless noted)

| Column | Predicate (all required) |
| --- | --- |
| `merged` | Linked PR `state=merged`, **or** issue `state=closed` with linked merged PR |
| `waiting_on_dieter` | `owner_wait.waiting`, or issue label `hold:*` (case-insensitive prefix) |
| `armed` | Open non-draft PR and (`auto_merge.enabled` or `pull_request.in_merge_queue`) |
| `review` | Open non-draft PR and (head `CHANGES_REQUESTED`, or `review_requested` without head `APPROVED`, or head `PENDING`, or `changes_requested_at_head` summary flag) |
| `pr_open` | Open PR (includes drafts) |
| `claimed` | Open issue, active lease (`claim.held`), no open PR |
| `issue_open` | Open issue, no lease, no open PR |

Staff runs never select a column in slice 1; they appear only as `staff_run`
source links for operator drill-down (#4321).

## Review and stale reviews

The **canonical head** is `pull_request.head_sha` when present; when the
reviews snapshot also carries `head_sha`, the two must match or the card is
`unknown`. Reviews are compared to that canonical head (exact head).

- A review whose `commit_id` **equals** the current head is **exact-head**.
- Any other `commit_id` is **stale** and listed in `stale_reviews` on the card.
  Stale reviews do not satisfy approval and do not clear the Review column.
- **Review column** matches when the PR is open, not draft, not merged, and any
  of: `review_requested` is true; an exact-head review has state
  `CHANGES_REQUESTED`; or there is no exact-head `APPROVED` while
  `review_requested` is true.
- An exact-head `APPROVED` from a required reviewer clears the review predicate
  unless `CHANGES_REQUESTED` is also present at head (changes win).

Checks (`green` / `pending` / `red`) are attached as source metadata only in
slice 1; they do not select the column except through future armed/merge-queue
rules already modeled on the PR snapshot.

## Waiting on Dieter

Matches when any of:

- `KanbanOwnerWaitSnapshot.waiting` is true (schedule hold, pending
  `staff.approve`, or owner-only blocker supplied by #4321).
- Issue carries a `hold:` label (case-insensitive), including fleet hold labels
  on the issue.
- Active schedule hold id appears in `owner_wait.reasons`.

While waiting, precedence can still lose to `merged` if merge evidence exists.

## Claimed vs Issue open

- **Claimed**: issue `state=open`, `KanbanClaimSnapshot.held` is true, no open PR.
- **Issue open**: issue `state=open`, claim not held, no open PR.

## Source links

Every `KanbanCard` includes `sources`: an ordered list of `KanbanSourceLink`
objects `{kind, label, url, available}`. Kinds include `issue`, `pull_request`,
`checks`, `review`, `auto_merge`, `claim`, `staff_run`, and `hold`. URLs are
whatever the snapshot provides (typically GitHub HTML URLs). Links are included
even when `available` is false so the UI can show broken provenance.

## Card shape

`project_kanban_column(snapshot) -> KanbanCard`:

| Field | Type | Notes |
| --- | --- | --- |
| `owner`, `repo`, `issue_number` | string / int | Identity |
| `column` | column id or `unknown` | |
| `column_label` | string | Human column name or `Unknown` |
| `matched_columns` | list | All predicates that matched, ordered by precedence (highest first) |
| `stale_reviews` | list | Stale review records |
| `reasons` | list | Fail-closed and informational strings |
| `sources` | list | Source links |

## Claims and staff runs (slice 3, #4320)

`backend/kanban_claims_ingestion.py` maps coordination claim checks and Staff
Hub runs into snapshot records without writing leases or claims:

- **Live lease** — `claims.check` reports `available` with an active lease (or
  board presence); `KanbanClaimSnapshot.held` is true.
- **Released / expired** — `held: false` from RM; card can be Issue open.
- **Bare `claim:*` label** — RM reports held with reason containing
  `(no active lease)`; snapshot is `available: false`, `degraded: true` so the
  projector shows **unknown** (never Claimed).
- **Incomplete history** — `available: false` from RM (comment/page limits, fail
  open, or incomplete pagination); same fail-closed **unknown** path.
- **Staff run** — newest active (or latest) run with `target_kind=issue` and
  `target_ref=#N` from `RunStore.list_runs` (read scan); exposed as a `staff_run`
  source link on the card.

Use `ingest_claim_and_staff(repo, issue, ...)` to fetch both snapshots in one
read-only call.

## Testing

Hermetic tests live in `tests/test_kanban_projection.py` with JSON fixtures
under `tests/fixtures/kanban/`. Each column has at least one fixture; additional
fixtures cover `unknown` and stale review cases.

Claims and staff ingestion behavior is covered in
`tests/test_kanban_claims_ingestion.py` (live lease, released lease, bare label,
degraded history, staff run link, no-write guard).
