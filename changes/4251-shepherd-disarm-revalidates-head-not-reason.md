---
issue: 4251
summary: "execute any shepherd-planned disarm: revalidate on head and kind, not on the rejected_at_head reason"
dl_state: "in_review"
next_step: "exact-head CI, then arm through automerge_guard"
owner: "qwen-ct-1"
branch: "qwen-ct-1/4251-disarm-revalidate-detail"
paths: "scripts/automerge_guard.py,scripts/merge_shepherd_pass.py,tests/unit"
---

# Merge shepherd disarms were unexecutable for every reason but one

The scheduled `Merge shepherd` pass failed more than 20 consecutive runs on `main`
(2026-11-28, e.g. run `46124351262` at 08:09Z) and left AffineDrift#5781 auto-merge
armed while deciding, every run, that it must come out of auto-merge.

Two gates agreed on one reason string and nothing else:

- `scripts/merge_shepherd_pass.py::_revalidate_disarm` re-planned the repo and required
  the row's `detail` to be exactly `rejected_at_head`. `plan_repo_disarms` also returns
  disarms for the six `suppression_detail` reasons
  (`deterministic_merge_group`, `incomplete_check_page`, `incomplete_queue_history`,
  `manual_dequeue`, `owner_unarmed`, `incomplete_comment_history`), so those revalidations
  always failed and `disarm_for_shepherd` raised `disarm revalidation failed` before it
  touched anything.
- `scripts/automerge_guard.py::disarm_for_shepherd` then only pulled auto-merge when
  `detail == "rejected_at_head"` or a hold applied, so a suppression-reason disarm would
  have been a silent no-op even past the first gate.

Revalidation's real contract is the one its own test names
(`test_disarm_refuses_new_head_after_planning`): refuse when the head moved since the plan.
It now matches on the disarm row's `kind`, PR and `head_sha`, and the guard clears the
disposition for any disarm the planner still confirms at that head. `detail` is still
passed through unchanged, so the queue-only dequeue path and the hold annotation
(`verdict.describe()` rather than the plan reason) keep their existing behaviour, and the
guard's disarm marker is now posted only when the disarm actually succeeded.
