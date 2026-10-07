# Current handoff — SC-G6: mobile Maxwell page drops its chat (DL-#1338-maxwell-mobile)

- **Repository / worktree:** Runner_Dashboard, `Runner_Dashboard-worktrees/claude-1338`; branch `claude/issue-1338`; commit SELF; PR: see the branch (draft).
- **Change:** `pages/Maxwell/Mobile.tsx` loses chat state, the send handler and `<MaxwellChat>`, and gains the "Chat with Maxwell in the Staff Console" link (same target as `MaxwellPage.tsx`). `MaxwellChat.tsx`, `QUICK_CHIPS` and the sessionStorage chat helpers are deleted; `ChatBubble`, `ChatMessage` and `CODEBASE_QUICK_CHIPS` stay (used by `CodebaseChat`).
- **Tests:** `Mobile.test.tsx` chat cases replaced by no-chat-input, link and no-chat-call cases; the integrity test and mobile viewport profile markers updated.
- **Validation:** vitest `frontend/src/pages/Maxwell frontend/src/shell` 315 passed; `tsc -p tsconfig.app.json --noEmit` clean; `pytest tests/test_frontend_integrity.py tests/test_mobile_test_harness.py` passed.
- **Spotted, not changed:** `storage.ts` `MAXWELL_CHAT_HISTORY` key (still cleared by `clearChatHistory`) and `.maxwell-chat-messages` CSS are now unused.
- **Next:** merge through the queue.

---

# Current handoff — Spec Check updates a stale warning comment (DL-#1871, #1871)

- **Repository / worktree:** Runner_Dashboard, `/home/user/wt/rd-1872-p2`; branch `fix/1871-spec-warning-update`, cut from #1872's head while #1872 was in the merge queue, then merged with `main` after #1872 merged; commit SELF; PR: see the branch (follow-up to #1872).
- **Change:** the "Post warning comment" step paginates the PR comments and, when a bot `SPEC.md Update Required` comment exists with a different body, PATCHes it (`issues.updateComment`); it creates one when none exists and does nothing when identical. Addresses the Codex P2 review on #1872.
- **Validation:** `pytest tests/test_spec_check_workflow.py tests/test_workflow_hygiene.py tests/test_workflow_action_pinning.py tests/test_workflow_runner_routing.py` → 179 passed (new node-executed tests; the update case was RED first); `actionlint` clean; ruff clean.
- **API snapshot:** `frontend/src/lib/openapi.json` / `api-types.ts` regenerated with `bash scripts/gen-api-client.sh` (identical to #1876, so it no-ops once #1876 lands).
- **Next:** merge the follow-up PR through the queue.

---

# Current handoff — CI-fix routing on current models, agy option (DL-#1880, #1880)

- **Repository / worktree:** Runner_Dashboard, `_worktrees/RD-ci-fix-routing`; branch `fix/ci-fix-routing-models` from `origin/main`; commit SELF; PR: see the branch. Lease: `claude` / `claude-rd-1880`.
- **Change:** `route_ci_fix` (`backend/ci_fix_dispatch.py`) no longer routes to `claude-3-7-opus` / `claude-3-5-sonnet` / `gpt-5-codex`.
  - **Escalated:** `claude-opus-5-5`.
  - **Lint:** Codex `gpt-6-luna` (the fast/affordable model in the Codex models list; `gpt-6.1-sol` is the workhorse).
  - **Tests/logic:** `antigravity` + `gemini-3.8-flash-high` (the fleet tier model, Repository_Management `conductor/tiers.py`) when `staff.adapters.ADAPTERS["antigravity"].unattended`, else `claude-sonnet-5-5`.
- **Key decision:** agy is gated on the staff adapter flag rather than a new switch. agy 1.2.11 headless auto-denies shell commands without a permission bypass, so the adapter is chat-only and Repository_Management `dispatch_cli_agent` refuses agy (#1800). The route therefore resolves to Sonnet 5.5 today and switches to agy automatically when the adapter becomes unattended-capable.
- **Validation:** RED: 5 route assertions failed on the old ids or the missing gate. GREEN: `pytest tests/api/test_ci_fix_dispatch.py tests/test_dispatch_effort.py` 38 passed; ruff and mypy clean.
- **Not changed (filed #1881, tier:strong):** RD-1 has no trigger (no caller, and no GitHub event intake); `/api/remediation/ci-fix/dispatch` launches no agent; `CIFixLockManager` locks never expire.
- **Spotted, not changed:** `agent_remediation/provider_registry.py` model lists (`claude-opus-4`, `gpt-5-codex`, `gemini-2.5-*`), `dispatch_routing.DEFAULT_SONNET_MODEL = "claude-sonnet-5"`, and `staff/pricing.DEFAULT_MODEL` are also stale.
- **Next:** merge through the queue.

---

# Current handoff — staff proposals tests leak real run workers (DL-#1863, #1863)

- **Repository / worktree:** Runner_Dashboard, `_worktrees/RD-staff-threads-teardown`; branch `test/staff-threads-runner-leak` from `origin/main`; commit SELF; PR: see the branch. Lease: `claude` / `claude-rd-1863`.
- **Why:** `test_staff_threads_api.py::test_mark_thread_read_and_inbox` errored at teardown in the full pre-push run ("test created a real git worktree", plus `Cannot operate on a closed database` in `StaffRunner._worker`). The worker came from `tests/api/test_staff_proposals_api.py`: three tests execute an approved `staff.dispatch` proposal (`ad-hoc`, `Repository_Management`), which called the real `StaffRunner.launch`. The daemon thread outlived the test, so it reached `_prepare_workdir` inside a later test, or between tests with the hermetic workspace patches undone, where it can clone into `~/Repositories/_staff_clones/` and run a real `git worktree add`.
- **Second leaker (found by the full suite):** `test_staff_thread_runs.py` needs-input tests (`answer_needs_input` and the `/answer` endpoint) submitted a continuation run. Its worker ran a real `gh repo clone D-sorganization/UpstreamDrift` into `~/_staff_clones` (exit 4) and then crashed on the closed store.
- **Change (test-only):** a shared `staff_launches` fixture in `tests/conftest.py` records `StaffRunner.launch` and fails at teardown if a test leaves a `staff-run-*` thread alive. The proposals fixture and the two needs-input tests use it and assert their launches.
- **Validation:** RED: the new guard failed the three dispatch tests. GREEN: `pytest tests/api/test_staff_proposals_api.py` 10 passed. proposals + thread-runs + threads files 49 passed ×3 with `-W error::pytest.PytestUnhandledThreadExceptionWarning`. With the stub disabled, the guard fails all five leaking tests. The full pre-push suite on the first commit passed twice, but logged the thread-runs leak; after the fix, the full pre-push suite (`pytest tests/ -m "not slow and not integration"`) passed 3/3 with zero unhandled staff-run thread exceptions, `closed database` errors or `gh repo clone` attempts.
- **Host cleanup:** `git worktree prune -v` in `~/Repositories/_staff_clones/Repository_Management` found no stray `run-866d30501c48` / `run-0ca29e8099fc` registrations (only `main` is registered). A stray test-made clone, `~/_staff_clones/Repository_Management`, exists outside the repos root; its 21 prunable registrations were pruned and 21 merged test `staff/ad-hoc-task-*` branches deleted. Left for the owner: the clone itself, plus `staff/ad-hoc-task-2f9534`, still checked out (clean) at `~/Repositories/_staff_worktrees/Repository_Management-run-368affe2d8f6`.
- **Next:** merge through the queue.

---

# Current handoff — detect-secrets baseline for #1873 (DL-#1871, #1871)

- **Repository / worktree:** Runner_Dashboard, `/home/user/wt/rd-1873-ds`; branch `fix/1873-secrets-baseline`, cut from #1873's head (`chore/1871-gitleaks-staged`) while #1873 is in the merge queue, with `origin/main` merged in; commit SELF; PR: not created (pushed to #1873's branch only if the queue drops it, otherwise a follow-up PR).
- **Cause:** `detect-secrets (baseline diff)` scans the PR merge ref. `main` gained #1868's `.pre-commit-config.yaml` change after #1873 branched, so in the merged tree the pinned detect-secrets `rev` SHA sits on line 83, not the 82 #1873 recorded. No new secret-like string.
- **Change:** `.secrets.baseline` regenerated with `detect-secrets==1.5.0` and the workflow's `--exclude-files`; only that finding's `line_number` (82 → 83) and `generated_at` changed.
- **Validation:** the workflow's "Audit baseline integrity" step, run locally on the merged tree, failed before (`line_number 82 → 83`) and passes after ("Baseline results unchanged").
- **Main re-merge:** latest `main` (#1872) merged again; the baseline check still passes with no line shift, and `frontend/src/lib/openapi.json` / `api-types.ts` are regenerated with `bash scripts/gen-api-client.sh` (identical to #1876). DEVELOPMENT_LOG carries a staged `No material development-log change` note.
- **Development log:** No material development-log change — baseline metadata only; DL-#1871 is kept byte-identical across #1872 / #1873.
- **Next:** if the queue drops #1873, push this branch to `chore/1871-gitleaks-staged`; otherwise open a follow-up PR after #1873 merges (re-run the scan first if `main` moved `.pre-commit-config.yaml` again).

---

# Current handoff (parallel PR) — staged-only gitleaks at commit (DL-#1871, #1871)

- **Repository / worktree:** Runner_Dashboard, `/home/user/wt/rd-1871-gl`; branch `chore/1871-gitleaks-staged`; commit SELF; PR: see the branch. The Spec Check workflow half ships alone as #1872 (`ci/1871-spec-check-backend`).
- **Change:** `.pre-commit-config.yaml` gitleaks hook runs `gitleaks protect --staged` (was `detect --source .`, a whole-history scan at every commit). `ci-secrets.yml` is unchanged and keeps its full-history `detect` scan. `.secrets.baseline` line number for the detect-secrets `rev` SHA moved 81 → 82 after the comment grew by one line.
- **Validation:** `pytest tests/test_workflow_hygiene.py tests/test_workflow_action_pinning.py` → 155 passed (staged-only test RED first; CI full-history guard added as a regression pin). Local `gitleaks protect --staged` (v8.24.0) on this commit's staged diff scanned ~1 KB in 0.5 s, no leaks.
- **Next:** merge through the queue.

---

# Current handoff — re-sync vendored run_pytest_diff (DL-#1864, Repository_Management#1929)

- **Repository / worktree:** Runner_Dashboard, `/home/user/wt/rd-1929-sync`; branch `chore/1929-resync-run-pytest-diff`; commit SELF; PR: see the branch.
- **Change:** `scripts/run_pytest_diff.py` is re-synced from Repository_Management `shared_scripts/run_pytest_diff.py` at `48893688` (branch `fix/1929-pytest-diff-collectible`, RM PR #1930), which upstreamed this repo's two #1868 "Local fix" changes. Only the `scripts.` import and 120-column ruff format differ from upstream. Behaviour is unchanged.
- **Validation:** `pytest tests/test_run_pytest_diff.py` → 20 passed (new no-fork test RED first against the forked copy); ruff and mypy clean.
- **API snapshot:** `frontend/src/lib/openapi.json` / `api-types.ts` regenerated with `bash scripts/gen-api-client.sh` after merging main (identical to #1876; no-ops once #1876 lands).
- **Next:** once RM #1930 merges, re-pin the vendored-from header to its merge SHA (one-line change).

---

# Current handoff — Spec Check covers backend/\*\* (DL-#1871, #1871)

- **Repository / worktree:** Runner_Dashboard, `/home/user/wt/rd-1871`; branch `ci/1871-spec-check-backend`; commit SELF; PR: see the branch. Workflow-only change, shipped alone; the staged-only gitleaks pre-commit change is a separate PR on `chore/1871-gitleaks-staged`.
- **Change:** `ci-spec-check.yml` adds `backend/*` (bash `[[ == ]]` globs match nested paths) to the source patterns; the PR comment drops "Bump the Spec Version" and uses the Repository_Management wording (one change-log row keyed by the PR; Spec Version is release-derived).
- **Validation:** `pytest tests/test_spec_check_workflow.py tests/test_workflow_hygiene.py tests/test_workflow_action_pinning.py tests/test_workflow_runner_routing.py` → 175 passed (3 new tests RED first; they execute the detection step against fixture file lists); `actionlint` clean.
- **Next:** merge through the queue; add `changes/<issue>-*.md` fragment acceptance after Repository_Management#1922 / #1924 reach this repo.

---

# Current handoff — event-tiered CI Standard (DL-#1864, #1864)

- **Repository / worktree:** Runner_Dashboard, `/home/user/wt/rd-ci-tiers`; branch `ci/1864-event-tiered-ci`; commit SELF; PR: see the branch. Workflow-only change, shipped alone (pre-push parity is a separate PR on `chore/1864-prepush-parity`).
- **Change:** new `changes` job in `ci-standard.yml` outputs `tier` / `full_suite` / `run_python_tests`. `pull_request` = lint, format, mypy, fast tests (no coverage/bandit/pip-audit/security-scan); `merge_group` and `workflow_dispatch` = full suite; `push` = no heavy jobs. The python-scope detector moved from `ci-health-check` into `changes`, runs on `pull_request` only, and fails closed when the files API returns a full page (100). `quality-gate` and `tests-required` read `needs.changes.result` and fail closed; nothing may skip in the full tier.
- **Key decision:** the coverage floor (`fail_under = 60`) is now enforced only in `merge_group`, where coverage is measured; the queue is the authoritative gate.
- **Validation:** `pytest tests/test_workflow_hygiene.py tests/test_ci_config.py tests/test_required_checks_drift.py tests/test_mypy_override_ratchet.py tests/test_fleet_merge_checker.py` → 206 passed (new tier tests RED first; they execute the tier, quality-gate and tests-required bash under each event). Pre-existing: `scripts/check_local_only_workflows.py` fails identically on `main` (hosted-runner allowlist lives in `local-only-runner-guard.yml`).
- **Next:** merge through the queue; confirm the first `merge_group` run shows security-scan and coverage, and a docs-only PR's required checks finish in under 3 min.

---

# Current handoff — fleet merge policy covers all 41 queue repos (DL-#1890, Repository_Management#1900)

- **Repository / worktree:** Runner_Dashboard, `_worktrees/RD-1900-fleet-policy`; branch `ci/1900-fleet-merge-policy-all-repos`; commit SELF; PR #1862.
- **Change:** `config/fleet_merge_policy.json` lists every merge-queue repository (41, rollout of 2026-10-04). `fetch_live_repo_snapshot` resolves protection on the repo's own default branch (Florida-Compressor/Controls `master`, Florida-Superheater `feat/superheater-feed-design`, half-ton-controls `initial-import`) and treats only `active` workflows as present, so disabled or deleted Auto-Update PRs workflows are not drift.
- **Validation:** `python -m pytest tests/test_fleet_merge_checker.py tests/test_required_checks_drift.py` → 35 passed (two new/changed tests RED first).
- **Next:** merge; run `python scripts/check_fleet_merge_settings.py` live once to confirm a clean fleet.

---

# Current handoff (parallel PR) — one spec-check workflow (DL-#1916, Repository_Management#1916)

- **Repository / worktree:** Runner_Dashboard, `/home/user/wt/rd-spec-check`; branch `ci/1916-merge-spec-check`; commit SELF; PR: see the branch. Workflow-only change, shipped alone.
- **Change:** removed `spec-check-enhanced.yml`; `ci-spec-check.yml` ("Spec Check") already covered every pattern (src/, tests/, config/, pyproject.toml, Cargo.toml, package.json, requirements.txt) plus CMakeLists.txt and uv.lock. Dropped the removed file from the `local-only-runner-guard.yml` allowlist and `config/workflow_runner_routing_policy.json`.
- **Not changed:** neither workflow is a required context and neither triggers on `merge_group` (no `merge_group` trigger existed to keep); `config/required_status_checks_policy.json` is untouched.
- **Validation:** `pytest tests/test_spec_check_workflow.py` → 13 passed (2 RED first); `pytest tests/test_workflow_runner_routing.py tests/test_workflow_hygiene.py tests/test_ci_config.py` passes; `python scripts/check_workflow_runner_routing.py` → 0 violations.
- **Next:** merge through the queue.

---

# Prior handoff — merge-queue check timeout raised to 180 min (DL-#1890, Repository_Management#1900)

- **Repository / worktree:** Runner_Dashboard, `_worktrees/RD-1900-timeout`; branch `ci/1900-queue-timeout-180`; commit SELF; PR #1859.
- **Change:** owner raised `check_response_timeout_minutes` to 180 on every fleet merge-queue ruleset (runner pool at ~93% timed out queued checks at 60). Both policy files and their test fixtures now expect 180, so the drift checkers stay clean.
- **Pre-push fix:** bandit B310 on `backend/fleet_merge_checker.py:_github_api` (from #1855) failed every push; the function now refuses any URL outside `https://api.github.com/` and carries `# nosec B310`.
- **Validation:** `python -m pytest tests/test_fleet_merge_checker.py tests/test_required_checks_drift.py` → 34 passed; `bandit -c bandit.yaml backend/fleet_merge_checker.py` → 0 issues.
- **Next:** merge; `config/fleet_merge_policy.json` still lists 8 of the ~41 queue-enabled repos (four use non-`main` default branches) — expanding it is a follow-up.

---

# Current handoff (parallel PR) — pre-push parity: no bandit, diff-scoped pytest (DL-#1864, #1864)

- **Repository / worktree:** Runner_Dashboard, `/home/user/wt/rd-prepush`; branch `chore/1864-prepush-parity`; commit SELF; PR: see the branch. Separate from the CI-tier PR (`ci/1864-event-tiered-ci`).
- **Change:** `.pre-commit-config.yaml` drops the pre-push `bandit` hook (CI Standard's lint job runs bandit) and `pytest-unit` now runs `uv run python -m scripts.run_pytest_diff`, keeping `language: system`. `scripts/run_pytest_diff.py` and `scripts/run_mypy_diff.py` are vendored from Repository_Management `shared_scripts/` at `ed046eb` (imports rewritten to `scripts.`, ruff-formatted); `python -m` avoids the RM#1912 import fault. No fallback directory: a change with no mapped test runs no pytest at pre-push.
- **Development log:** No material development-log change — DL-#1864 (added on `ci/1864-event-tiered-ci`) already lists this branch and its paths.
- **Validation:** `pytest tests/test_run_pytest_diff.py tests/test_workflow_hygiene.py` → 134 passed (hook-wiring tests RED first). One-file push simulation (`backend/dispatch_routing.py` → `tests/test_dispatch_routing.py`) ran in about 1 s.
- **Review fixes (#1868, Codex):** `run_pytest_diff` now targets only collectible `test_*.py`/`*_test.py` modules — a changed `conftest.py` or test helper runs its directory (`tests/` for the root conftest) — and always accumulates name matches, so `backend/gh_client.py` runs `tests/test_gh_client.py` and `tests/test_gh_client_retry.py`. Both are marked `Local fix` in the vendored file; Repository_Management `shared_scripts/run_pytest_diff.py` has the same two defects and needs the upstream fix before the next re-sync. 19 passed in `tests/test_run_pytest_diff.py` (4 new, RED first).
- **Secrets baseline:** `.secrets.baseline` refreshed with `detect-secrets==1.5.0 scan --baseline` (CI's command); the only change is the already-audited `.pre-commit-config.yaml` detect-secrets rev SHA moving from line 81 to 82.
- **Next:** merge; re-sync the vendored files when Repository_Management changes `shared_scripts/run_pytest_diff.py` (e.g. #1912).

---

# Prior handoff — required-checks policy for the merge queue (DL-#1890)

- **Repository / worktree:** Runner_Dashboard, `_worktrees/RD-1890-policy`; branch `ci/1890-required-checks-policy-merge-queue`; commit SELF; PR: see DL-#1890.
- **Change:** policy requires the merge queue and `require_branches_up_to_date: false`; the drift checker gains `merge_queue_drift` / `up_to_date_drift`. New fixture `tests/contracts/branch_protection_snapshot_merge_queue.json`; the compliant example ruleset now carries the queue.
- **Validation:** `tests/test_required_checks_drift.py` 16 passed; `tests/test_workflow_hygiene.py` passes. Live run: only the #1119 `guard` gap remains (`guard` is not a required context; making it one is an owner settings decision).
- **Next:** merge; wire the live check into #1850.

---

# Current handoff (parallel PR) — USE-1: effort per dispatch kind, epic expansion, weekly usage report (DL-#1865, #1865)

- **Repository / worktree:** Runner_Dashboard, `/home/user/wt/rd-use1`; branch `feat/1865-effort-usage-report`; commit SELF; PR: see the branch.
- **Change:** `dispatch_effort.resolve_effort(kind, override)` (unknown → medium; explicit override validated). `CommandEnvelope.effort` validated in `__post_init__`/`from_dict`; `build_envelope(effort=)` puts it in the signed payload. `CIFixRoute.effort` (lint low, test medium, escalated high). PR/issue dispatch requests take `dispatch_kind` + optional `effort` and pass both as workflow inputs and in the audit history. `expand_epic_children` issue dispatches route to tier:cli (Sonnet) with `epic_expansion.render_expansion_prompt`, whose child template links `cli_tier_task.md`, `pr-lifecycle.md` and `AGENT_TIER_ROUTING.md` instead of repeating them. `usage_report` renders `SessionTelemetryStore.get_metrics_window` (this week vs previous week) and `post_weekly_usage_report` finds-or-creates the `usage-report` issue "Weekly agent usage report" in Repository_Management and creates/updates one comment per ISO week (marker `<!-- usage-report:week=YYYY-Www -->`). `POST /api/usage/report/weekly` (admin scope, `dry_run`) and an opt-in 6-hourly loop (`USAGE_REPORT_WEEKLY_ENABLED=1` on one node) run it. `gh_client.patch` added.
- **Not done / follow-ups:** RM's `Agent-PR-Action.yml` / `Agent-Issue-Action.yml` do not exist in Repository_Management today, so no launcher consumes the `effort` input yet; merge-queue wait/timeouts are rendered as "not reported" until a source exists; the issue is not created yet (first run creates it) and pinning is manual (GraphQL-only).
- **Validation:** `pytest tests/test_dispatch_effort.py tests/test_epic_expansion.py tests/test_agent_dispatch_router.py` → 49 passed (effort/router tests RED first); `pytest tests/test_usage_report.py tests/api/test_usage_report_routes.py tests/test_session_telemetry.py tests/test_usage_metrics.py` passes; `ruff check backend/ clients/`, `ruff format --check`, `mypy backend/` clean.
- **Next:** merge; enable the loop on the hub node and pin the issue.

---

# Prior handoff — merge queue pilot verification (Repository_Management#1900) — done: #1853 merged through the queue 2026-10-04, merge_group CI Standard success

- **Repository / worktree:** Runner_Dashboard, `_worktrees/RD-1900-queue-test`; branch `docs/1900-merge-queue-test-typo`; commit SELF; PR: see the branch.
- **State:** `main` now has a repo-level merge-queue ruleset (squash, build 5, group 1–5, 5 min wait, ALLGREEN, 60 min timeout), strict up-to-date off in classic protection, `delete_branch_on_merge` on. Snapshot: Repository_Management `docs/operations/settings-snapshots/Runner_Dashboard-2026-10-03.json`.
- **Change:** CONTRIBUTING.md documents merging through the queue. This PR is the pilot's end-to-end check. No material development-log change — docs note only; tracked under Repository_Management DL-#1900.
- **Next:** confirm this PR merges through the queue with a `merge_group` CI run; 48h pilot watch before other repos.

---

# Prior handoff — merge_group triggers for the merge queue (DL-#1890) — merged #1852

- **Repository / worktree:** Runner_Dashboard, `_worktrees/RD-1890-merge-group`
- **Branch:** `ci/1890-merge-group-triggers` from `origin/main`; commit SELF; PR: see DL-#1890.
- **Issue:** D-sorganization/Repository_Management#1890 (epic #1889). Workflow-only change, shipped alone.
- **Changes:** `merge_group:` on CI Standard and Anti-Phantom Merge Guard; anti-phantom concurrency group falls back to `github.ref`; new hygiene test requires the trigger on every required-context workflow.
- **Validation:** RED then GREEN, 148 passed (`tests/test_workflow_hygiene.py`, `tests/test_ci_config.py`).
- **Next:** merge; then the #1900 admin pilot enables the merge queue and turns off strict up-to-date protection. `util-auto-update-prs.yml` stays until later in #1890.

---

# Prior handoff — stats route test date-rot fix (merged #1851)

- **Repository / worktree:** Runner_Dashboard, `_worktrees/RD-stats-date-bomb`; branch `fix/stats-route-test-date-bomb` from `origin/main`; commit SELF; PR: see the branch.
- **Why:** `tests/api/test_workflow_stats_routes.py` seeded runs at a fixed `2026-09-20`; the summary endpoint reads the last 14 days, so the test began failing on 2026-10-04 UTC and blocked every pre-push and PR CI run (found while pushing the Repository_Management#1890 merge_group PR).
- **Change:** the seed defaults to one day before now. Test-only; no runtime change. No material development-log change — test-only date-rot fix with no feature entry.
- **Validation:** `pytest tests/api/test_workflow_stats_routes.py`: 11 passed.
- **Next:** merge; then rebase and push `ci/1890-merge-group-triggers`.

---

# Current handoff — container scan repair (DL-#1840)

- Worktree `C:/Users/diete/Repositories/Runner_Dashboard-worktrees/codex-1840`, branch `fix/issue-1840-container-security`, issue #1840, PR #1841, lease session codex-rd-overnight-1840. Production unchanged; peer checkout preserved.
- Release older-head Docker run36813181457/job110212483375 failed fixable HIGH findings. Gemini 3.8 Flash CLI drafted TDD changes; second CLI review verified delta and identified an overstated docstring, corrected.
- RED four assertions; GREEN63Windows hardening and71Linux hardening/HTTP tests using isolated frozen dependencies. Docker build and matching Trivy0.70 scan exit0. Logs `/home/dieterolson/.cache/rd1840-docker-build.log`, `rd1840-trivy-scan.log`, `rd1840-tests.log`. Official Trivy archive checksum verified before execution.
- Only urllib3 lock changed2.7→2.8; exact OpenSSL/PCRE Debian pins move deb13u2→u3. Preserve base digest/runtime/hash enforcement/scan gates. No suppression or broad upgrades.
- Next: focused PR, required CI and guarded merge; merge protected main into release draft afterward. Release phone/root/env/standby/Python-policy approvals remain pending in release worktree preflight. Do not mark goal complete from tests.

---

