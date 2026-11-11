# Repository_Management sync bundle for #4315

Copy into `Repository_Management` on branch `cursor/4315-merge-path-fix-needed-1d39`:

- `shared_scripts/independent_exact_head_review.py` → `shared_scripts/`
- `scripts/automerge_guard.py` → `scripts/` (merge with RM-specific paths; keep RD-CI-005 arm-cap imports only on RD)
- `tests/test_in_flight_enqueue_hold.py` → RM test tree (or port the covered case into existing `test_automerge_guard.py`)

Fleet agents without access to the private RM repo should open the companion PR via an owner-authenticated host (`gh-personal`).

## Fail-closed audit (bundled with #4316)

Paths verified in `scripts/automerge_guard.py`:

- `evaluate_hold`: malformed/missing PR payload; missing `head.sha` always runs signal 5; PR/files/timeline/comments/commits API errors → held; `HoldVerdict.describe()` surfaces `error` when reasons are empty
- `disarm_if_held`: inherits `evaluate_hold` fail-closed; disarms when held
- `_merge_clean_pr`: refuses merge when head SHA cannot be re-read; refuses head drift; post-PUT in-flight hold → exit 5
- `_enqueue_clean_pr`: refuses when `head_sha` or node missing; pre/post in-flight holds
- CLI `main` / `_arm_cli`: held → exit 1; in-flight hold → exit 5; identity/cap exits unchanged
- `enqueue_stalled_pr`: uses `disarm_if_held` (not bare `evaluate_hold`)
- `_base_has_merge_queue`: unknown base or rules API error → treat as queue present (fail closed)
- `independent_review_hold_reason`: empty head, unreadable, missing PASS at head for agent/reviewed PRs
