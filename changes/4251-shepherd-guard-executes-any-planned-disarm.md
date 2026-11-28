---
issue: 4251
summary: "execute any shepherd-planned disarm in automerge_guard, not only rejected_at_head or a hold"
dl_state: "in_review"
next_step: "exact-head CI, then arm through automerge_guard"
owner: "qwen-ct-1"
branch: "qwen-ct-1/4251-disarm-revalidate-detail"
paths: "scripts/automerge_guard.py,tests/unit"
---

# Merge shepherd disarms were still unexecutable for every reason but one

Gate 1 of this bug is fixed on `main`: #4738 dropped the `detail == "rejected_at_head"`
comparison from `scripts/merge_shepherd_pass.py::_revalidate_disarm`, so the planner's
requeue-suppression reasons now survive revalidation.

Gate 2 did not. `scripts/automerge_guard.py::disarm_for_shepherd` only pulled auto-merge
when the reason _was_ `rejected_at_head` or a hold applied, so an unheld suppression
disarm still reached `after == before` and the scheduled pass kept failing.

Measured on `main` after #4738 merged at 08:35:51Z. Runs `46128887719` (09:06:21Z) and
`46130281545` (09:23:54Z) both run on head `deabf1f4f` — #4738 itself — and both are red:

```
merge shepherd disarm repo=D-sorganization/Gasification_Model pr=5939 disarmed=False exit_code=1
  {"pr": 5939, "detail": "incomplete_comment_history", "hold_held": false,
   "before_auto_merge": true, "after_auto_merge": true, "before_in_merge_queue": true,
   "after_in_merge_queue": true, "disarmed": false, "exit_code": 1}
```

`hold_held` is false and the reason is not `rejected_at_head`, so the outer gate skipped
all three of its branches and Gasification_Model#5939 stayed auto-merge armed.

Reaching the guard _is_ the authorization: `_revalidate_disarm` already confirmed the
planner still wants this PR out of auto-merge at this exact head, and the guard still
raises `disarm revalidation failed` when the head moved. `detail` is a report field, not
a permission, so the guard now branches on the disposition it read instead of on the
reason that produced it:

- armed → disarm the arm, mark it when the route succeeded;
- unarmed but queued → the queued entry would still merge, so attempt and mark;
- unarmed and not queued → nothing to clear, so no route call and no marker.

That last branch is deliberate. `gh pr merge --disable-auto` is idempotent and returns
success when nothing is armed, so a guard that keyed off `verdict.held` alone would post
"The merge guard disarmed auto-merge" on PRs it never touched. Per `_mark_guard_disarm`'s
own docstring that marker makes a PR read as held until the next push, so it has to stay
accurate.

`detail` is still passed through unchanged and the hold annotation keeps using
`verdict.describe()` rather than the plan reason, so the queue-only dequeue path and the
existing hold wording are unaffected.
