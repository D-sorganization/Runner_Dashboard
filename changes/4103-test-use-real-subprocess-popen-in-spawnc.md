---
issue: 4103
summary: "test: use real subprocess.Popen in SpawnCounter bench test (avoids double-count with provider guard)"
---

## Handoff

- **Branch:** `cursor/spawn-counter-popen-guard-8bb6`
- **Change:** Capture `subprocess.Popen` before the autouse provider guard wraps it (`tests/real_subprocess_popen.py`), use that handle in `_no_real_provider_cli` and in `test_spawn_counter_separates_gh_from_other` via `SpawnCounter(REAL_SUBPROCESS_POPEN)`.
- **Why:** Without this, `SpawnCounter.installed()` patches the guard wrapper so each spawn increments `other` twice (expected `(1, 3)` vs `(1, 1)`), failing `tests` / `quality-gate` on PRs that exercise the suite (e.g. backlog spawn work).
- **Validation:** Local `pre_pr` could not run `detect-secrets` (module missing in this VM); pytest not installed here. CI should re-run the standard matrix.
