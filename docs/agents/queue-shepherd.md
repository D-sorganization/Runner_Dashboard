# queue-shepherd: dry-run merge-queue triage

queue-shepherd is a read-only, dry-run advisor for the Runner_Dashboard merge
queue (issue #2929). It reuses slice 1's pure dependency-order and
path-conflict checks (`shared_scripts/queue_shepherd_logic.py`) through
slice 2's read-only GraphQL poll (`shared_scripts/queue_shepherd_poll.py`) and
reports its findings — it never acts on them.

Role file: `docs/agents/role-drafts/queue-shepherd.yml` — a schema-valid draft in
this repo; the fleet's real role source is `Repository_Management/staff/roles/`
(see `backend/staff/roles.py`), so publishing it there is a follow-up for the
operator/coordinator. (Not placed at this repo's own root `staff/roles/`: that
path would create a top-level `staff/` directory that collides with the
`backend/staff` package's import namespace and breaks `ruff`'s import sorting
across every `from staff...` import in `backend/`.)

## What it does

1. Polls the merge queue and open auto-merge-armed PRs on Runner_Dashboard
   (`poll_queue`), roughly every 5 minutes.
2. Flags PRs whose declared dependency sits further back in the queue
   (`find_dependency_order_violations`) and PRs that overlap changed files
   with another PR queued ahead of them (`find_path_conflicts`).
3. Renders a short recommendation (`render_brief`) and reports it to rd-barb:
   which PR to move ahead, or which PR to pull before it ejects a whole
   merge group.
4. Surfaces the same data on the fleet brief (`GET /api/v1/fleet/brief`,
   `queue_shepherd` field / "Queue-shepherd:" markdown line) so an operator
   or dashboard surface can see queue state without polling it separately.

## What it never does (dry-run, always)

- Never reorders the queue, jumps a PR to the front, or cancels a
  `merge_group` run or a push run on main. A jump-to-front proposal always
  needs the operator's own admin login; queue-shepherd only names the
  candidate and the reason.
- Never arms, disarms, or merges a PR.
- Never duplicates slice 1's dependency-order or conflict-detection logic —
  it only calls it.

## Before it runs unattended

Per the issue's acceptance criteria, one supervised dry run on Runner_Dashboard
is required before this role is scheduled unattended: an operator (or Barb)
reads a run's proposals against the live queue and confirms they are sound.
That sign-off is an operational step for after this role ships, not something
the role (or this PR) can execute itself.
