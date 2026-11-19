### Added
- Default-off hub-owned review-arm service under pr-remediator (Refs #4249; epic #4251): fail-closed intake of green PRs across the fleet repos, independent Codex/Claude review dispatch via `dispatch_staff_run`, verdict recording (recovered from run events after a hub restart), sticky blocked heads, holds, findings relay, round>4 / round>=2-still-blocking owner escalation and arm-only auto-merge (`enablePullRequestAutoMerge` pinned to the reviewed head; unacknowledged file deletions refused).
- Wired into the staff scheduler bootstrap behind `RD_REVIEW_ARM_HOST` (only that machine runs it; dry-run by default) with `GET /api/staff/review-arm/status` and a stall alert. One-time import of the box's `arm_holds.txt`, `blocked_heads.txt` and `arm_override.txt`.
- A 7-day replay of the box's ledger requires identical arm decisions.

### Fixed
- Startup reconcile now relaunches queued runs stranded by a hub restart (or fails them visibly) instead of leaving them queued with no worker.
- `scripts/automerge_guard.arm_auto_merge` is arm-only: a clean-status refusal no longer falls back to a direct merge or enqueue.
