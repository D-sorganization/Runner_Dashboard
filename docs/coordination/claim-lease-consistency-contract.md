# Claim/lease read consistency contract

Issue [#4156](https://github.com/D-sorganization/Runner_Dashboard/issues/4156). On
November 7, 2026 PST, dispatch run `run-aa9257137cdb` was denied (`lease_blocked`)
by the admission check, and a later operator claim lookup on the same issue
reported it unheld. This document is the acceptance item that defines what
"agree" and "legitimately disagree" mean for that pair of reads, what evidence
an operator sees today, and a bounded way to re-verify the contract later. It
does **not** close #4156: the issue's broader original-incident
preservation/correlation item (recovering and correlating `run-aa9257137cdb`'s
specific event data) is out of scope for this slice and remains open. Three
PRs already landed against #4156 (all `Refs #4156`, issue left open): #4157
(fenced block-diagnostics event), #4160 (the reproduction matrix this document
cites), #4170 (lifecycle-fence re-assertion at prompt-execute time).

## 1. The two code paths

- **Dispatch admission**: `backend/staff/lease.py::acquire()`. Before starting a
  run it calls RM's `check_agent_claim` subprocess once
  (`coordination.rm_scripts.run_module("check_agent_claim", ...)`), and
  `_blocks_on_claim_check()` decides from that single response whether to set
  `failure_class="lease_blocked"` and return `None` (the run never starts).
- **Operator/public claim lookup**: `backend/coordination/claims.py::check()`
  (and `claim()`, which calls `check()` first). It calls the *same* RM
  `check_agent_claim` subprocess, independently, plus (only on an un-held
  result) an in-process 60-second coordination-board cache read
  (`_board_claim_override` / `board.cached_board_state`) that can override a
  "free" `check_agent_claim` answer to "held" from stale board presence.

Both call sites shell out to the same external, mutable source — RM's GitHub
issue comments/labels — as independent, uncached, live reads. Neither call is
memoized, neither carries an epoch/version number, and there is no shared
snapshot between them: `lease.acquire()`'s read and a later
`coordination.claims.check()` read are two separate point-in-time photographs
of the same live system, taken seconds or minutes apart, with no mechanism to
compare "as of" markers between them. (`claims.check()`'s own board-cache layer
is the *one* place a cached read exists at all, and it only ever makes a "free"
result stricter — "held" — never looser, see §2.)

## 2. What "agree" means, and legitimate divergence vs. a bug

**Agreement** means: a `check_agent_claim` read taken at wall-clock time *T1*
during admission, and a `check_agent_claim` (or claim-API) read taken at *T2 >
T1* during an operator lookup, report compatible holder state for the issue,
where "compatible" allows for any state change whose timestamp lies between
*T1* and *T2* (a release, an expiry, a renewal). Two reads are not required to
report the identical holder — only a holder-state transition that is
consistent with time passing.

Legitimate reasons the two reads can diverge seconds apart (each reproduced in
`tests/unit/test_claim_lease_cross_path_consistency.py`, see §4):

- **Expiry boundary.** The lease's `expires_at` falls between *T1* and *T2*;
  RM's `check_agent_claim` naturally reports the same lease as held, then
  expired/unheld. This is exactly #4156's reported incident, reproduced in
  `test_expire_release_race_between_admission_block_and_later_lookup`.
- **Release race.** The holder releases
  (`coordination.claims.release()` / RM's `release_agent_lease`) between the
  two reads. Same test covers this by flipping the fake `check_agent_claim`
  response between the admission call and the lookup call.
- **Same-owner-renewal asymmetry.** The two paths define "is this my own
  hold?" differently, and that difference alone can make one path block while
  the other calls it a renewal for the *same* `(agent, session)` pair at
  nearly the same instant:
  - `coordination.claims.py::_is_own()` treats any live read whose
    `(agent, session)` matches the caller as a renewal — unconditionally.
  - `staff/lease.py::acquire()` only exempts a hold via
    `dispatch_self_claim.is_own_dispatch_claim_hold()`, which requires the
    session to match dispatch's own synthesized
    `expected_staff_session(run_id)` (`f"staff-{run_id}"`) **and** a
    `claim_guard`-recorded, dispatch-pre-applied `claim:<role>` label on that
    exact `run_id` — not a general "this agent+session already holds it"
    fact. A lease posted some other way for the identical agent+session (for
    example, by the operator claim API) still blocks dispatch admission.
    `tests/unit/test_claim_lease_cross_path_consistency.py::test_same_agent_session_hold_is_not_symmetric_across_paths`
    reproduces this: the admission path blocks (`failure_class ==
    "lease_blocked"`) on the same `(agent, session)` that `claims.claim()`
    treats as `claimed: True`. This is a documented asymmetry in "my own
    hold" semantics, not a correctness bug in either path — each path's
    definition is locally correct for what it is guarding (dispatch admission
    vs. operator claim renewal).
- **Board-cache staleness making a lookup *stricter*, not looser.** The one
  cached read in this system, `coordination.claims.check()`'s
  `_board_claim_override`, can turn a live "free" `check_agent_claim` answer
  into a reported "held" when the 60-second board-presence cache still lists
  an active session (`test_stale_board_cache_can_diverge_from_a_live_check_agent_claim_read`).
  This is the inverse of #4156's reported direction (held-then-free); the
  cache fails closed, never open.

**What would actually indicate a bug**, as opposed to legitimate divergence of
independent live reads:
- A **normalization mismatch** — the two paths disagreeing about the same
  `repo#issue` key because of case folding, a `D-sorganization/` prefix
  inconsistency, or a different `issue` string-vs-int representation, such
  that they are not actually asking RM the same question. Both paths in this
  codebase call the identical `check_agent_claim --repo <repo> --issue
  <issue>` subprocess shape, so this class is believed absent today, but
  nothing in the current tests pins the exact repo-string normalization that
  both call sites apply before invoking it — see the gap noted in §4.
- A **stale read with no cache-invalidation path** — i.e., a cache that can go
  stale and is never invalidated or time-boxed. `claims.check()`'s board cache
  has an explicit TTL and a write-time invalidation (`board.py`'s cache is
  cleared on every coordination write and a generation counter is bumped), so
  it is a bounded, intentional cache, not an unbounded one. If a future change
  introduced a cache on the `lease.acquire()` side (there is none today) with
  no analogous invalidation, that would be the kind of change this contract
  calls a bug, not legitimate divergence.
- Either call site **silently swallowing a `check_agent_claim` failure as
  "free"** rather than failing open explicitly. `claims.check()` already
  distinguishes `FAIL_OPEN_PREFIX` ("error:...") from a genuine `held: false`
  and reports `available: False` rather than treating a read failure as
  "unheld"; `lease.acquire()`'s `_blocks_on_claim_check()` only blocks when
  `check.get("held")` is literally true. A change that collapsed "RM could not
  be reached" into "free" on either path would be a bug.

## 3. What evidence an operator sees today for a block decision

When `staff/lease.py::acquire()` blocks a run, `_record_block_diagnostics()`
(added by #4157) writes a `job_board` activity envelope via
`activity_producers.record_job_claim_activity(kind="conflict", ...)` — the same
stream `coordination.claims.claim()` already writes "conflict" events to on a
held-by-someone-else claim attempt (DRY: one stream, not a second log). The
envelope carries, in addition to the standard job-claim fields
(`repo`, `issue`, `session`, `agent`, `holder_principal`, `fencing_token_id`):

| Field | Meaning |
| --- | --- |
| `run_id` | The dispatch run that was blocked. |
| `decision` | Always `"lease_blocked"` for this event. |
| `held_reason` | The RM-reported reason, sanitized by `_sanitize_reason()`: path-like substrings become `<redacted-path>`, secret/token-like substrings (`ghp_`, `ghs_`, `github_pat_`, `Bearer ...`) become `<redacted-token>`, truncated to 300 chars, defaulting to `"unknown"`. |
| `held_expires_at` | The lease's reported `expires_at`, or `"unknown"` if RM did not report one. |
| `observed_at` | Wall-clock UTC timestamp (`datetime.now(UTC).isoformat()`) of *this* read — the *T1* in §2's agreement definition. |
| `storage_backend` | Always `"rm.check_agent_claim"` today — names the authoritative store this read came from. |

`holder_principal` is derived as `f"agent-{held_agent}"` from the RM-reported
`agent` field, or `"unknown"` when RM reports none — never a raw token,
session secret, or filesystem path (those are exactly what `_sanitize_reason`
strips from the free-text `reason`).

This is sufficient to answer "was it really held, and when" for an operator
comparing a block decision against a later "unheld" lookup: the operator reads
`observed_at` and `held_expires_at` from the conflict event, compares them to
the later lookup's own timestamp, and can conclude "the lease was reported
held as of `observed_at`, expired/was releasable by `held_expires_at`, and the
later lookup happened after that" — without ever seeing a holder token, a
session secret, or a private filesystem path. What it does **not** give an
operator: the *exact* instant the holder actually released (only this read's
own `observed_at`, and the lease's own reported expiry) — that is inherent to
two independent uncached reads with no shared epoch, not a gap in this
evidence envelope.

## 4. Reproduction and diagnostic evidence on file

Deterministic reproduction, no production code changed, same `FakeRM` canned
responses used for both call sites — `tests/unit/test_claim_lease_cross_path_consistency.py`:

- `test_claim_free_admission_agrees_across_both_paths`
- `test_active_conflicting_claim_denied_on_both_paths`
- `test_same_agent_session_hold_is_not_symmetric_across_paths`
- `test_dispatch_pre_applied_label_hold_is_recognized_as_own_by_lease_acquire`
- `test_expire_release_race_between_admission_block_and_later_lookup`
- `test_stale_board_cache_can_diverge_from_a_live_check_agent_claim_read`

Plus the block-diagnostics coverage in `tests/unit/test_staff_lease_dispatch_self_claim.py`:

- `test_acquire_continues_when_check_reports_dispatch_label_hold`
- `test_acquire_still_blocks_foreign_agent_hold`
- `test_acquire_still_blocks_merged_pr_guard_even_with_dispatch_label`
- `test_acquire_block_records_fenced_diagnostics_for_later_claim_lookup`
- `test_acquire_block_sanitizes_path_and_token_like_reasons`
- `test_acquire_block_diagnostics_skip_cleanly_on_non_numeric_issue`
- `test_is_own_dispatch_claim_hold_unit`

**Exact-SHA green run**, this branch, `git rev-parse HEAD` =
`f477c850b99a2f1b2b639503bdc5b332e52d29a0`:

```
$ python -m pytest tests/unit/test_claim_lease_cross_path_consistency.py tests/unit/test_staff_lease_dispatch_self_claim.py -v
...
tests/unit/test_claim_lease_cross_path_consistency.py ......             [ 46%]
tests/unit/test_staff_lease_dispatch_self_claim.py .......               [100%]
============================= 13 passed in 8.30s ==============================
```

(13 = 6 + 7, run with Python 3.11.14; this repo requires `>=3.11,<3.14` and the
sandbox's default `python3` is 3.10, which fails to import `datetime.UTC` —
use a 3.11+ interpreter, e.g. the project's own CI image or a local `.venv`.)

**Known gap, stated honestly rather than patched with a new test here**: no
test in either file pins the exact string normalization (`repo` casing, the
`D-sorganization/<repo>` prefix, `issue` as `str` vs. `int`) that
`staff/lease.py::acquire()` and `coordination/claims.py::check()` each apply
before calling `check_agent_claim`, so a future normalization mismatch between
the two call sites (§2's first "actual bug" category) would not be caught by
the current suite. Closing that gap is its own test-writing slice, not done by
this documentation PR.

## 5. Operator validation plan

A human or agent operator can re-verify this contract still holds after a
future change to either code path by running, from the repository root with a
Python 3.11+ interpreter:

```
python -m pytest tests/unit/test_claim_lease_cross_path_consistency.py tests/unit/test_staff_lease_dispatch_self_claim.py -v
```

- **Green** (all 13 pass): the reproduction matrix in §4 and the fenced
  block-diagnostics envelope in §3 still hold as documented here.
- **Red**: re-read the failing test's docstring first — each test in
  `test_claim_lease_cross_path_consistency.py` states which scenario and which
  acceptance item it characterizes. A failure means either (a) the change
  altered one path's "is this held / is this my own hold" semantics in a way
  this document no longer describes accurately (update §2's asymmetry
  description, or the fix, to match the new intended behavior — don't just
  silence the test), or (b) the change broke the block-diagnostics envelope's
  shape or sanitization in §3 (fix the envelope, since it is the only safe
  evidence an operator has for reconciling a block against a later lookup).
- This is a read-only check. It does not authorize, and must never be used to
  justify, a lease override, a force-release, borrowing another session's
  token, retrying the original blocked dispatch, a hub deploy/restart, or any
  credential change — all explicitly out of scope for #4156.
- If the gap in §4 (normalization pinning) becomes load-bearing for a future
  change, open a new issue referencing #4156 rather than writing that test as
  an unreviewed addition to this document.
