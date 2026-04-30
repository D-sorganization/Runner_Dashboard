# 0002. Dispatch envelope contract for hub-to-node commands

## Status

Accepted — implemented in `backend/dispatch_contract.py` (schema `dispatch-envelope.v1`, envelope version `1`).

## Context

The dashboard issues commands that affect real fleet state: restart runner services, stop or unregister units, modify scheduled-maintenance timers, dispatch AI agents to PRs and issues, and trigger deployed-update flows. These commands originate at the dashboard "hub" (the operator's browser session, via the FastAPI server) and execute on remote fleet nodes or via `gh workflow run`.

Earlier prototypes passed loosely-shaped JSON between hub and node. That created several problems:

- No allowlist — any string the frontend sent was eligible to run.
- No separation between read-only health probes and privileged, destructive actions.
- No human-confirmation gating on destructive actions like `runner.stop` or `service.unregister`.
- No replay protection: a stolen request body could be re-sent indefinitely.
- No audit trail: it was impossible to reconstruct who approved what and when.
- No schema versioning: rolling forward the envelope shape risked silently breaking older callers.

These violate the project's "Design by Contract" engineering principle, which the project root `CLAUDE.md` explicitly calls out as mandatory on dispatch envelopes and any `POST` route.

## Decision

Define a single, JSON-serialisable **dispatch envelope** contract in `backend/dispatch_contract.py` and require every hub-to-node command to flow through it. Key elements of the contract:

1. **Schema versioning.** `SCHEMA_VERSION = "dispatch-envelope.v1"` and `ENVELOPE_VERSION = 1`. Validation rejects anything with a different `schema_version`.
2. **Allowlist of actions.** `ALLOWLISTED_ACTIONS` maps each action name (e.g. `runner.restart`, `agents.dispatch.issue`, `dashboard.update_and_restart`) to a `DispatchAction` record with explicit `access` (`READ_ONLY` | `PRIVILEGED`), human description, prototype command, and `requires_confirmation` flag. Anything not in the allowlist is rejected.
3. **Privilege separation.** `DispatchAccess.READ_ONLY` actions (e.g. `runner.status`, `scheduler.list`) skip confirmation; `DispatchAccess.PRIVILEGED` actions require a `DispatchConfirmation` recording `approved_by`, `approved_at`, optional `approval_hmac`, and `note`.
4. **Cryptographic signing.** Every `CommandEnvelope` is signed with HMAC-SHA256 over a canonical JSON projection of `(action, source, target, requested_by, issued_at, envelope_version, principal, on_behalf_of, correlation_id)`. The signing secret is loaded from `DISPATCH_SIGNING_SECRET` or persisted at `~/.config/runner-dashboard/dispatch_signing_key` (mode `0600`).
5. **Freshness windows.** `validate_envelope_crypto` rejects envelopes whose `issued_at` (or `confirmation.approved_at`) is more than ±300 s from current time, providing replay protection.
6. **Audit trail.** Every accepted or rejected envelope produces a `DispatchAuditLogEntry` with `event_id`, `envelope_id`, `decision`, `confirmation_state`, `args_hash` (SHA-256 of payload), `principal`, `on_behalf_of`, `correlation_id`, and `recorded_at`.
7. **Side-effect free module.** The contract module performs no I/O against the running service so it is fully unit-testable; route handlers in `backend/server.py` and `backend/routers/*` consume the contract.

`POST` handlers for runner control, agent dispatch, scheduler modification, and deployed-update flows all build envelopes via `build_envelope(...)`, run `validate_envelope(...)` and `validate_envelope_crypto(...)`, and emit `build_audit_log_entry(...)` before executing the prototype command.

## Consequences

**Easier:**

- A single test surface (`tests/api/`) can exercise every dispatch path without spinning up a runner host — the contract is pure data.
- Frontend payload generators and backend validators share the same shape; mismatches are caught at boundary parsing instead of in shell.
- Adding a new privileged action is a one-line `ALLOWLISTED_ACTIONS` change plus the corresponding handler — and it automatically inherits confirmation gating and audit logging.
- The audit log gives operators and post-incident reviewers a complete answer to "who dispatched what, when, and was it approved".

**Harder / cost:**

- Frontend callers must construct envelopes with matching field names; ad-hoc `POST {action: "..."}` no longer works.
- Schema evolution requires a two-step rollout (additive ship → removal) per the "Reversible" engineering principle. Bumping `dispatch-envelope.v1` to `v2` will require a parallel-acceptance window where both versions validate.
- The signing secret is now operationally significant: losing it invalidates in-flight envelopes; rotating it requires a coordinated restart across hub and any signature-checking nodes.
- Every privileged action now requires a real human-approval record, which slows down certain dashboard flows (by design) and adds UI complexity for the confirmation prompt.

Future work: extend the contract to cover async/streaming responses (currently each dispatch is a one-shot RPC), and consider lifting the envelope into `Repository_Management/shared_scripts/` if `Maxwell-Daemon` adopts the same protocol — see the "DRY" engineering principle.
