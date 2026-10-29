---
feature: "Bot-builder slice 12 form-first UI flow, fleet bot list, standalone approval screen, and version history/rollback (RD #3106)"
---
- Add `backend/staff/bot_builder/ui.py` implementing the v1 form-only builder UI flow (`DraftMode.TEMPLATE`, `SCRATCH`, `CLONE`, `EDIT`), fleet bot list (`render_fleet_bot_list`), standalone approval screen with cumulative/per-version diffs and unbundled-edit enforcement (`build_approval_screen`), version history/rollback (`render_version_history`), and supervised dry-run review/activation (`review_dry_run`, `BuilderWorkspaceController`), covered by unit tests in `tests/unit/test_bot_builder_ui.py`.
