# ADR 0010: Independent verifier at the exact head SHA (#3300 upgrade 1)

Status: accepted. Expert Panel verdict 4-0, two rounds (thread `th_b01730d15a37`),
recorded on Runner_Dashboard#3300: "Verifier at SHA: adopt. Key the verdict to the
tree hash and record the commit SHA. A merge-group tree that differs needs a fresh
verdict. Self-verify is refused. A timeout sets STALE_UNVERIFIED: the PR leaves the
queue but stays open, and is never auto-accepted." First of the four ADR/spec docs
in #3300's build order; each of the five upgrades gets its own implementation PR
after its doc merges, in the order posted on #3300.

## Context

`backend/staff/verification.py` already judges a PR, but only against CI: `decide()`
reads `pr.ci` (green/pending/red) and the PR's open/merged/closed state, and
`verify_and_record` persists that as the run's `verification` field. Nothing today
records that a second agent actually read the diff. `backend/code_requests/acceptance.py`
reuses the same `decide()` for the `executing -> done` gate. Barb currently arms
auto-merge once CI is green; there is no step where an agent other than the PR's
own builder is required to accept or reject it first.

## Decision

Add a **review** step ahead of auto-merge, independent of and in addition to the
existing CI-only `verified` verdict:

- An agent other than the PR's builder records an explicit `accept` or `reject`
  against the PR's current **tree hash**, with the **commit SHA** recorded alongside
  it. The tree hash, not just the commit SHA, is the key: the merge queue rebuilds a
  merge-group tree that can differ from the PR branch's own tree, and that rebuilt
  tree needs its own fresh verdict rather than inheriting the branch-tip review.
- **Self-verify is refused.** The reviewer's agent/session identity must differ from
  the identity recorded as the PR's builder. A matching identity is rejected, not
  silently accepted.
- **Reviewer preference, not requirement:** a different vendor/provider is preferred
  over the builder's own provider when more than one reviewer is available, but is
  not a hard gate — same-provider review by a different session is still a valid
  accept.
- **Timeout is `STALE_UNVERIFIED`, not acceptance.** A review that does not complete
  within the review window leaves the PR out of the merge-queue candidate set (it is
  not armed) but the PR itself stays open; nothing auto-accepts it on timeout.
- **Barb arms auto-merge only after a recorded `accept`** against the PR's current
  tree hash — CI green is necessary but no longer sufficient on its own.
- A `reject` records the builder-facing reason as a short code
  (e.g. `TEST_FAILURE`, `BEHAVIOR_MISMATCH`) plus free-text detail, so the ledger
  upgrade (#3300 upgrade 4) can key off it later.

## Invariants (to be enforced in code and tests by the implementation PR)

- I1 a review verdict's reviewer identity never equals the PR's recorded builder
  identity; a self-review attempt is rejected before it is persisted.
- I2 a review verdict is valid only for the exact tree hash it was recorded against;
  if the PR's tree hash changes (a new push, or a merge-group rebuild against a
  moved `main`), the prior verdict no longer authorizes arming and a fresh review is
  required.
- I3 a review timeout persists as `STALE_UNVERIFIED`; it is never upgraded to
  `accept` by the passage of time alone, and the PR is never auto-armed from that
  state.
- I4 `automerge_guard.py` (or whatever arms the PR) checks for a current `accept`
  verdict before arming, in addition to the existing CI check; a `reject` or missing
  verdict blocks arming the same way red CI does today.
- I5 this review step is additive: it does not replace or remove the existing
  CI-only `verified`/`unverified`/`failed`/`no_change`/`not_applicable` verdicts in
  `staff.verification.decide()`, which continue to answer "did this run produce a
  green, mergeable PR" independent of whether anyone has reviewed it yet.

## Planned schema and code (additive; implemented in the next #3300 PR, not this one)

- `backend/staff/verification.py`: a `reviewed` verdict path alongside the existing
  CI-only ones, keyed on `(commit_sha, tree_sha)`; a reviewer-identity-vs-builder-
  identity check; a `STALE_UNVERIFIED` outcome for a timed-out review.
- `backend/code_requests/acceptance.py`: `unmet_conditions` gains a review condition
  for a child's PR alongside the existing merged/CI/criteria conditions, reusing the
  same PR lookup plumbing.
- A reason-code enum shared with the future friction-ledger upgrade (#3300 upgrade 4),
  so `reject` codes can be keyed and deduplicated later without a schema change.

This ADR is scope and contract only; no production code changes in this PR.

## Consequences and follow-ups

- The implementation PR (next in the #3300 build order, after this doc and the
  other three upgrade docs merge) adds the actual review recording, the identity
  check, and the arm-time gate in `automerge_guard.py`.
- #3300 upgrade 2 (bar frozen at claim time) and upgrade 3 (lane-scoped claims,
  amending ADR 0008) are separate docs; this ADR does not depend on them, but the
  review record's reviewer/builder identities reuse the same session-identity
  concept that ADR 0003's lease comment format and ADR 0008's authority model
  already use.
- Not in scope here: who counts as an eligible reviewer pool, and whether a review
  can be requested automatically versus opportunistically by whichever second agent
  next looks at the PR — both are implementation-PR decisions, not architecture.
