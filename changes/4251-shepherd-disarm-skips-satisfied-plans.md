---
issue: 4251
summary: "merge-shepherd executor skips the guard round trip for a disarm plan that is already satisfied"
dl_state: "in_review"
next_step: "exact-head CI, then arm through automerge_guard"
owner: "qwen-oglaptop-1"
branch: "qwen-oglaptop-1/4251-disarm-noop-clear"
paths: "scripts/merge_shepherd_pass.py,tests/unit"
---

# Merge shepherd went red disarming pull requests that were already clear

#4737 made the executor honour every planned disarm reason, which is right — but the
executor decides *how* to honour one only after `automerge_guard.disarm_for_shepherd` has
already read the hold state and revalidated the whole repository through GraphQL. The
disposition that decides whether anything has to happen at all is the first thing that
function reads, so for a pull request that is neither armed nor queued the pass spent a
per-PR hold evaluation plus a repository-wide GraphQL (`_revalidate_disarm` re-runs
`plan_repo_disarms` over every open PR) on a no-op, and a transient failure on those reads
made the scheduled job exit 1.

Measured on `main` at `dd10a47f`: Merge shepherd runs 1198-1202 are red, five in a row, on
the same three rows.

```
merge shepherd disarm repo=D-sorganization/UpstreamDrift pr=15922 disarmed=False exit_code=1
  {"pr": 15922, "error": "gh: HTTP 502", "disarmed": false, "exit_code": 1}
  {"pr": 15939, "error": "unexpected end of JSON input", "disarmed": false, "exit_code": 1}
```

All three were planned for `incomplete_check_page`, and all three already reported
`autoMergeRequest: null` with `mergeStateStatus: CLEAN` at exactly the planned head, so
GitHub had nothing to disarm and `disarm_for_shepherd` would have issued no route call.
The `disarmed=False` came from a read that never needed to happen.

`_disarm_via_guard` now reads the disposition itself and returns a satisfied plan as
satisfied before touching the guard. Fail-closed is unchanged: `read_merge_disposition`
answers `(True, True)` when it cannot read the arm, so an unreadable pull request still
takes the full guard path, and a genuinely armed pull request whose revalidation fails
still reports `disarmed=False` and keeps the pass red.

Skipping those reads also removes the pass's largest recurring GraphQL cost on
UpstreamDrift — three repository-wide queries every 20 minutes for work already done —
which is the pressure behind the `Resource limits for this query exceeded` error that made
run 1197 plan those disarms in the first place.
