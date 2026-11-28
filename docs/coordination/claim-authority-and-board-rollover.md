# Claim authority and RM #2539 rollover

Part of #4270. The serving hub's SQLite compare-and-set is the claim. GitHub
issue comments and `claim:*` labels are a mirror. RM #2539 is at GitHub's
2500-comment cap (`comments: 2500` on the open issue "Fleet coordination board
successor: recover exhausted #2376"). A mirror write that cannot land must not
drop a claim the hub already won, and rollover must not delete that history.

## Serving hub (authoritative)

The process serving the hub API holds `CLAIM_STATE_DB` (see
`backend/coordination/claim_state.py`). Two write paths take that compare-and-set
before they mirror to GitHub:

| Entry | Code |
| --- | --- |
| `POST /api/coordination/claims` | `coordination.service.claim_issue` → `claims.claim` |
| Job-board claim | `coordination.service.claim_job` → `jobs.claim_job` |

`claims.claim` calls `acquire_compare_and_set`. A second session loses with
`ClaimHeldError`. The same agent and session may renew the row and retry the
mirror. `jobs.claim_job` uses the same compare-and-set, then `post_lease`.

Release (`POST /api/coordination/claims/release` → `claims.release`) still
clears the SQLite row after the mirror release. A job claim that posted the
mirror and then failed to store its fence or lease row still rolls both back
(`jobs._undo_unrecorded_claim`). That path is local bookkeeping, not a mirror
failure.

## CLI mirror (not the compare-and-set)

These Repository_Management modules read and write GitHub only. They do not
take the hub SQLite lock. A spoke that runs them without `POST /api/coordination/claims`
does not win the serving-hub claim:

- `scripts.check_agent_claim`
- `scripts.post_agent_lease`
- `scripts.release_agent_lease`

`staff.lease.acquire` still blocks a dispatch run when `post_agent_lease` fails.
That admission check is not the hub compare-and-set.

## Mirror failure

After the row is held, `post_agent_lease` may fail (HTTP 502, timeout, or a
comment rejected because the board is full). The hub:

- re-raises the mirror error to the caller
- may still roll back an orphan `claim:*` label on GitHub (`release_agent_lease`)
- leaves the SQLite row held for the winning agent and session

The winner retries the mirror. Any other session loses the compare-and-set
until the winner releases the row or the reservation TTL passes.

## RM #2539 rollover

`backend/coordination/postman.py` treats 2500 comments as full. That is
GitHub's per-issue comment cap, and #2539 is on it now. New coordination
comments on that issue will fail. Do not delete comments, and do not delete
the issue, to make room.

`execute_board_rollover` (default threshold 500, already exceeded on #2539):

- creates a successor issue and points `board_issue` at it
- keeps the retired issue number in `board_predecessors` (the config list
  keeps at most three ids; dropping an older id from that list does not delete
  the GitHub issue or its comments)
- posts a notice on the retired issue when GitHub accepts it
- does not call issue delete or comment delete

History stays on #2539. The active board pointer moves. Claim authority stays
in the serving hub's SQLite file, so a full board cannot erase a held claim.
