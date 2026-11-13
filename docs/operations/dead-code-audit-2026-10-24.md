# Dead code and legacy surface audit (2026-10-24)

Product intent: [`docs/project/CHARTER.md`](../project/CHARTER.md), [`docs/project/STATUS.md`](../project/STATUS.md).

Scope: backend, `scripts/`, workflows, config, docs — not frontend UI components.

Evidence: `rg` reference scans, `python -m vulture backend/ scripts/ --min-confidence 80` with `PYTHONPATH=backend`, router mounts in `backend/server.py`, retirement tests under `tests/test_retired_*` and `tests/test_legacy_*`.

## Findings

| Path | Evidence dead / stale | Risk if wrong | Action |
|------|----------------------|---------------|--------|
| `backend/quick_dispatch.py` | `POST /api/agents/quick-dispatch` is HTTP 410 (`backend/routers/remediation_retired.py`); module only imported by `backend/assistant_tools.py` and unit tests — not by Code Requests (`code_requests/agent_dispatcher.py`) | Low | **Remove** |
| `backend/assistant_tools.py` → `quick_dispatch_agent` | Allowlist still points at 410 route; executor called removed module | Low | **Remove** with module |
| `tests/test_quick_dispatch.py`, `tests/test_quick_dispatch_health_gate.py` | Only test removed module | None | **Remove** |
| `backend/routers/remediation_retired.py` | 410 Gone routes; `tests/test_legacy_dispatch_retirement.py` | Clients may probe until Sunset | **Keep** |
| `backend/routers/assistant.py` `POST /api/assistant/chat` | 410 (SC-D11); `AssistantSidebar.tsx` still POSTs (frontend lane) | Medium | **Keep** stub; **Dieter:** sidebar |
| `backend/routers/code_requests.py` `_DISPATCH_WORKFLOW` | Probe uses `Jules-Feature-Request.yml` (404); dispatch uses `Agent-Quick-Dispatch.yml` | Medium — stale `dispatchTarget` | **Dieter:** fix probe |
| `Jules-Assess-Repo.yml` paths | Active in assessments + staff work requests; Jules disabled in provider registry | Medium | **Keep** until Jules retired |
| `backend/routers/maxwell.py` | CHARTER F15; #1338 retirement in flight (`changes/1338-*`) | High | **Keep**; **Dieter:** #1338 order |
| `GET /api/cline/status` | Launcher retired (`tests/test_retired_cline_launcher.py`); credentials probe remains | Low | **Keep**; **Dieter:** drop later |
| `jules_cli` / `jules_api` in registry | `enabled: false` but listed (`tests/api/test_providers_registry.py`) | Low | **Keep** |
| `DEPRECATED_SEAT_PROVIDERS` (`gemini`, `grok-chat`) | Enforced (`tests/unit/test_staff_board_seat_providers.py`) | None | **Keep** |
| `backend/routers/feature_requests.py` | `sys.modules` alias; tests patch only | Low | **Remove** |
| `/api/feature-requests*` aliases | Deprecated wrappers in `code_requests.py` | Low | **Keep** until clients migrate |
| `DASHBOARD_ENABLE_CONDUCTOR` / `DASHBOARD_ENABLE_DELIVERY_METRICS` | Default-off feature flags; tested | None | **Keep** |
| `scripts/generate_agent_hook_adapters.py` | No CI; `shared_scripts` tested | Low | **Keep** CLI |
| `docs/archive/root-cleanup-2026-10-17/assessments/2026-04-26-comprehensive-assessment.md` | Duplicate of `docs/assessments/2026-04-26-comprehensive-assessment.md`; no inbound links | None | **Remove** |
| `docs/project/STATUS.md` | Steward run 2026-09-29; stale decisions | Confusion | **Dieter:** steward refresh |
| `docs/agents/repository-guide.md` | Legacy tab list vs Staff Console | Confusion | **Update** later |
| `SPEC.md` `/api/agent-launcher/*` | Router removed; SPEC not collated | Spec drift | **Dieter:** fragment row |
| `AGENT_HANDOFF.md` | Session handoff, not runtime | None | **Dieter:** archive |
| Vulture | Unused vars in `staff/chat.py`, `staff/maintenance.py`, `workflow_stats.py` | Low | Optional later |

## Dieter decisions

1. #1338 Maxwell vs CHARTER F15 proxy retention  
2. Code Requests availability probe workflow name  
3. Assistant sidebar vs 410 chat API  
4. When to delete 410 routes and OpenAPI entries  
5. SPEC / STATUS steward updates for retired surfaces  
6. Off-theme tabs listed in STATUS.md (#1338)

## Follow-up PRs

Non-stacked deletion PRs target rows marked **Remove** above (see linked PRs in the audit session handoff).
