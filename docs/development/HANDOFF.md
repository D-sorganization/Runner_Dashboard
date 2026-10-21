# Current handoff — Fleet hub, Brick, Codex defaults (#2022)

- **Repository / worktree:** Runner_Dashboard, `/workspace`; branch `cursor/fleet-hub-brick-codex-7386`; commit SELF; PR: not created.
- **Governing issue:** #2022 — DeskComputer hub, Brick in registry, repo Codex default model, dual-hub guard.
- **Completed:** Registry hub demotion/promotion; `apply_fleet_identity_from_registry` wired in `server.py` + `dashboard_config.apply_fleet_identity`; `proxy_utils` reads live `dashboard_config` attrs; `config/staff_provider_defaults.json` + classifier `config` class; runbooks under `docs/runbooks/`.
- **Validation:** `pytest tests/test_fleet_hub_topology.py tests/unit/test_staff_provider_defaults.py tests/unit/test_staff_classifier.py::test_classify_codex_unsupported_chatgpt_model_as_config tests/unit/test_staff_classifier.py::test_allowed_failure_classes_complete tests/api/test_staff_runner.py::test_build_command_substitutes_and_drops_empty_model_flag -q`; `ruff check backend/`; full CI pending on PR.
- **Operator follow-up:** Reconcile DeskComputer interim `HUB_URL` / `FLEET_NODES` env before restart (`docs/runbooks/fleet-hub-deskcomputer-rollout-2022.md`); redeploy Brick per `docs/runbooks/brick-dashboard-redeploy.md`.
- **Next step:** Open PR with `Closes #2022` and confirm CI Standard green.
