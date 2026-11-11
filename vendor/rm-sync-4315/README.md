# Repository_Management sync bundle for #4315

Copy into `Repository_Management` on branch `cursor/4315-merge-path-fix-needed-1d39`:

- `shared_scripts/independent_exact_head_review.py` → `shared_scripts/`
- `scripts/automerge_guard.py` → `scripts/` (merge with RM-specific paths; keep RD-CI-005 arm-cap imports only on RD)
- `tests/test_in_flight_enqueue_hold.py` → RM test tree (or port the covered case into existing `test_automerge_guard.py`)

Fleet agents without access to the private RM repo should open the companion PR via an owner-authenticated host (`gh-personal`).
