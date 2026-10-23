# Current Handoff — Fleet Hub, Brick, Codex Defaults (#2022)

## Barb Continuity Follow-Up (#2199, PR #2246)

- The summary parser now keeps recognized malformed, incomplete or ambiguous metadata out of persisted replies, matching the streaming filter. Quoted and nested examples remain ordinary reply text.
- Combined context/summary credentials require an explicit operator opt-in and separate provisioning of exactly `memory.read` plus `memory.shared.summarize`; do not grant generic `memory.write`. Each original caller also needs summary permission.
- Validation: three regression cases failed before the parser fix; all 38 summary/streaming/result tests pass afterward. Complete canonical and ordinary commit/pre-push checks before publication.
- Runtime acceptance remains pending the qualified canonical DeskComputer API deployment and actual provider save acknowledgement. Preserve its existing memory and conversations; no ControlTower-local fallback.

- **Repository / worktree:** Runner_Dashboard, `/workspace`; branch `cursor/fleet-hub-brick-codex-7386`; commit SELF; PR: https://github.com/D-sorganization/Runner_Dashboard/pull/2023 (ready).
- **Governing issue:** #2022 — DeskComputer hub, Brick in registry, repo Codex default model, dual-hub guard.
- **Completed:** Registry hub demotion/promotion; `apply_fleet_identity_from_registry` wired in `server.py` + `dashboard_config.apply_fleet_identity`; `proxy_utils` reads live `dashboard_config` attrs; `config/staff_provider_defaults.json` + classifier `config` class; runbooks under `docs/runbooks/`.
- **Validation:** `pytest tests/test_fleet_hub_topology.py tests/unit/test_staff_provider_defaults.py tests/unit/test_staff_classifier.py::test_classify_codex_unsupported_chatgpt_model_as_config tests/unit/test_staff_classifier.py::test_allowed_failure_classes_complete tests/api/test_staff_runner.py::test_build_command_substitutes_and_drops_empty_model_flag -q`; `ruff check backend/`; full CI pending on PR.
- **Operator follow-up:** Reconcile DeskComputer interim `HUB_URL` / `FLEET_NODES` env before restart (`docs/runbooks/fleet-hub-deskcomputer-rollout-2022.md`); redeploy Brick per `docs/runbooks/brick-dashboard-redeploy.md`.
- **Next step:** Open PR with `Closes #2022` and confirm CI Standard green.
