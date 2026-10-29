"""The one staff-dispatch policy shared by ``POST /api/staff/{role}/run`` and the
``staff.dispatch`` action (#1487).

``dispatch_staff_run`` owns everything a dispatch must pass through, in order:
PR-consolidation decision, plan validation, the ``dispatches`` rate limit, machine
targeting (``local`` / peer / ``auto``), peer forwarding with on-behalf-of provenance,
dry-run and the fail-closed ``dispatch`` audit record. Refusals are ``HTTPException``s
so the route returns them unchanged; the action executor classifies them with
:func:`failure_class_for`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import psutil
import system_utils
from fastapi import HTTPException
from identity import Principal, format_caller
from staff import claim_guard, consolidation
from staff import fleet as staff_fleet
from staff.audit import AuditError, record_audit
from staff.barb_lease_fence import BarbClaim, barb_write_fence
from staff.budget import BudgetGuard
from staff.rate_limit import check_rate_limit
from staff.runner import RunRequest, StaffRunner, get_runner
from staff.store import _now

log = logging.getLogger("dashboard.staff")

DEFAULT_DISPATCH_CPU_MAX_PCT = 85.0
DEFAULT_DISPATCH_MEM_MAX_PCT = 85.0
DEFAULT_DISPATCH_IO_PRESSURE_MAX_PCT = 35.0

_ORIG_PSUTIL_CPU_PERCENT = getattr(psutil, "cpu_percent", None)
_ORIG_PSUTIL_VIRTUAL_MEMORY = getattr(psutil, "virtual_memory", None)
_ORIG_GET_IO_PRESSURE_SNAPSHOT = getattr(system_utils, "get_io_pressure_snapshot", None)

_FAILURE_CLASS_BY_STATUS = {
    403: "permission_denied",
    422: "invalid_params",
    429: "rate_limited",
    502: "upstream_error",
    503: "peer_unreachable",
}


@dataclass(frozen=True)
class DispatchResourceSnapshot:
    """One-shot host CPU, memory, and PSI I/O-pressure reading for dispatch admission (#3553)."""

    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    io_pressure_full_avg10: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "cpu_percent": round(float(self.cpu_percent), 2),
            "mem_percent": round(float(self.mem_percent), 2),
            "io_pressure_full_avg10": round(float(self.io_pressure_full_avg10), 2),
        }


def _env_threshold(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError:
        log.warning("staff: invalid %s=%r; using default %.1f", name, raw, default)
        return default
    return val if val > 0 else default


def _extract_io_pressure_avg10(snapshot: dict[str, Any] | None) -> float:
    if not isinstance(snapshot, dict):
        return 0.0
    full_block = snapshot.get("full")
    if isinstance(full_block, dict) and full_block.get("avg10") is not None:
        return float(full_block["avg10"])
    if snapshot.get("full_avg10") is not None:
        return float(snapshot["full_avg10"])
    return 0.0


def sample_dispatch_resources() -> DispatchResourceSnapshot:
    """Sample local CPU %, memory %, and PSI full I/O pressure avg10 (#3553)."""
    in_pytest = "PYTEST_CURRENT_TEST" in os.environ
    enforce_live = os.environ.get("STAFF_DISPATCH_RESOURCE_GATE_ENFORCE_IN_TESTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    cpu_fn = getattr(psutil, "cpu_percent", None)
    mem_fn = getattr(psutil, "virtual_memory", None)
    io_fn = getattr(system_utils, "get_io_pressure_snapshot", None)

    cpu_pct = 0.0
    if callable(cpu_fn) and (not in_pytest or enforce_live or cpu_fn is not _ORIG_PSUTIL_CPU_PERCENT):
        try:
            cpu_pct = float(cpu_fn(interval=None))
        except Exception as exc:  # noqa: BLE001
            log.debug("staff: cpu_percent sample failed: %s", exc)

    mem_pct = 0.0
    if callable(mem_fn) and (not in_pytest or enforce_live or mem_fn is not _ORIG_PSUTIL_VIRTUAL_MEMORY):
        try:
            mem_pct = float(mem_fn().percent)
        except Exception as exc:  # noqa: BLE001
            log.debug("staff: virtual_memory sample failed: %s", exc)

    io_pct = 0.0
    if callable(io_fn) and (not in_pytest or enforce_live or io_fn is not _ORIG_GET_IO_PRESSURE_SNAPSHOT):
        try:
            io_pct = _extract_io_pressure_avg10(io_fn())
        except Exception as exc:  # noqa: BLE001
            log.debug("staff: io_pressure sample failed: %s", exc)

    return DispatchResourceSnapshot(
        cpu_percent=cpu_pct,
        mem_percent=mem_pct,
        io_pressure_full_avg10=io_pct,
    )


def check_dispatch_resource_admission(
    cmd: DispatchCommand,
    caller: Principal,
    *,
    machine: str = "local",
) -> None:
    """Reject local non-dry-run dispatches when CPU, RAM, or I/O pressure is saturated (#3553)."""
    if cmd.dry_run or machine != "local":
        return
    gate_flag = os.environ.get("STAFF_DISPATCH_RESOURCE_GATE", "1").strip().lower()
    if gate_flag in ("0", "false", "no", "off"):
        return

    max_cpu = _env_threshold("STAFF_DISPATCH_CPU_MAX_PCT", DEFAULT_DISPATCH_CPU_MAX_PCT)
    max_mem = _env_threshold("STAFF_DISPATCH_MEM_MAX_PCT", DEFAULT_DISPATCH_MEM_MAX_PCT)
    max_io = _env_threshold("STAFF_DISPATCH_IO_PRESSURE_MAX_PCT", DEFAULT_DISPATCH_IO_PRESSURE_MAX_PCT)

    snap = sample_dispatch_resources()
    breaches: list[str] = []
    if snap.cpu_percent >= max_cpu:
        breaches.append(f"cpu={snap.cpu_percent:.1f}%>={max_cpu:.1f}%")
    if snap.mem_percent >= max_mem:
        breaches.append(f"mem={snap.mem_percent:.1f}%>={max_mem:.1f}%")
    if snap.io_pressure_full_avg10 >= max_io:
        breaches.append(f"io_pressure={snap.io_pressure_full_avg10:.1f}%>={max_io:.1f}%")

    if not breaches:
        return

    thresholds = {
        "cpu_max_pct": max_cpu,
        "mem_max_pct": max_mem,
        "io_pressure_max_pct": max_io,
    }
    summary = ", ".join(breaches)
    log.warning("staff: dispatch of role=%s rejected due to resource saturation (%s)", cmd.role, summary)
    try:
        record_audit(
            action="dispatch",
            target=f"role:{cmd.role}",
            principal=format_caller(caller),
            on_behalf_of=cmd.on_behalf_of,
            surface=cmd.surface,
            thread_id=cmd.thread_id,
            outcome="rejected",
            detail={
                "code": "resource_saturated",
                "breaches": breaches,
                "metrics": snap.to_dict(),
                "thresholds": thresholds,
            },
            fail_closed=False,
        )
    except AuditError as exc:
        log.debug("staff: resource saturation audit skipped: %s", exc)

    raise HTTPException(
        status_code=503,
        detail={
            "code": "resource_saturated",
            "failure_class": "resource_exhausted",
            "message": f"local host resource saturation ({summary}); dispatch rejected",
            "retryable": True,
            "breaches": breaches,
            "metrics": snap.to_dict(),
            "thresholds": thresholds,
        },
    )


@dataclass(frozen=True)
class DispatchCommand:
    """A validated dispatch request, independent of the surface it came from.

    ``requested_by`` / ``on_behalf_of`` / ``surface`` / ``thread_id`` are provenance; the rest mirrors the /run body.
    """

    role: str
    requested_by: str
    provider: str | None = None
    model: str | None = None
    # Internal routing hint used by trusted callers such as CI-fix; never exposed as
    # an unauthenticated provider-selection surface.
    model_tier: str = ""
    repo: str = ""
    issue: int | None = None
    pr: int | None = None
    prompt: str = ""
    machine: str = "local"
    dry_run: bool = False
    work_item_id: str = ""
    on_behalf_of: str = ""
    surface: str = "api"
    thread_id: str = ""
    origin_node: str = ""
    # Dispatch even when the role's budget or the provider's plan window is spent (#1588); audited.
    ignore_budget: bool = False
    # One admitted run per operation (#1796): an Idempotency-Key reservation, or a peer's forward.
    operation_id: str = ""
    # PR lifecycle & subscription policy (#1845 / RD-0)
    pr_lifecycle: str = "arm_and_exit"
    auto_fix: bool = False
    operator_opt_in: bool = False
    wakeups_count: int = 0
    max_wakeups: int = 3
    # RD-1 CI fixes (#1881) target a PR head, not main: the issue premise ("already passes
    # on main") and prompt path-overlap checks do not apply; the per-PR CI-fix lock guards.
    skip_premise_check: bool = False
    # Fix an existing PR: the worktree starts from this branch instead of origin/main (#1881).
    head_ref: str = ""
    # Skill-matching hint for unattended dispatch (#2157); empty means infer from role/repo.
    task_type: str = ""
    standards_extra: str = ""
    requested_skill: str | None = None
    # Force duplicate run/PR: bypasses pre-dispatch open-PR and active-run guards (#2254).
    force_duplicate: bool = False
    # Operator-only (staff.admin) override of a foreign claim:* label / closing PR; needs a reason, audited (#2562).
    force_claim_override: bool = False
    claim_override_reason: str = ""
    execution_mode: str = "local"
    dispatch_origin: str = ""

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("a dispatch needs a role")
        if not self.requested_by.strip():
            raise ValueError("a dispatch needs a requested_by principal")

    def forward_body(self) -> dict[str, Any]:
        """The /run body a peer receives (provenance travels in the signed header)."""
        body = asdict(self)
        for key in ("role", "requested_by", "on_behalf_of"):
            body.pop(key)
        if not body["operation_id"]:
            body.pop("operation_id")  # peers on older builds never see an empty field
        if not body["skip_premise_check"]:
            body.pop("skip_premise_check")
        if not body["head_ref"]:
            body.pop("head_ref")
        if not body["model_tier"]:
            body.pop("model_tier")
        if not body.get("force_duplicate"):
            body.pop("force_duplicate", None)
        if not body["force_claim_override"]:
            body.pop("force_claim_override")
            body.pop("claim_override_reason")
        return body

    def run_id(self) -> str:
        """The run id this command admits: derived from ``operation_id`` when given."""
        if not self.operation_id:
            return f"run-{uuid.uuid4().hex[:12]}"
        op = self.operation_id
        return op if op.startswith("run-") else f"run-{op.removeprefix('op-')}"


def failure_class_for(exc: HTTPException) -> str:
    """Map a dispatch refusal onto the action failure taxonomy."""
    if isinstance(exc.detail, dict) and isinstance(exc.detail.get("failure_class"), str):
        return str(exc.detail["failure_class"])
    return _FAILURE_CLASS_BY_STATUS.get(exc.status_code, "dispatch_failed")


def refusal_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or detail)
    return str(detail)


async def _resolve_target(
    runner: StaffRunner,
    machine: str,
    provider: str,
    *,
    role: Any = None,
) -> str:
    """Machine targeting (#1197): ``local`` / this host → local; ``auto`` → least loaded
    online node with the provider installed; a peer name → that peer; unknown → 422."""
    peers = staff_fleet.peer_nodes()
    if machine.strip().lower() == "auto":
        board_view = await staff_fleet.aggregate_board(staff_fleet.local_board(runner), peers)
        if role is not None:
            from staff.failover_dispatch import pick_machine_for_chain
            from staff.provider_failover_config import resolve_failover_chain

            chain = resolve_failover_chain(role, requested_provider=provider)
            chosen = pick_machine_for_chain(board_view, chain, runner.machine)
        else:
            chosen = staff_fleet.choose_machine(board_view, provider, runner.machine)
        return "local" if chosen == runner.machine else chosen
    resolved = staff_fleet.resolve_machine(machine, runner.machine, peers)
    if resolved is None:
        known = ", ".join([runner.machine, *sorted(peers)]) or runner.machine
        raise HTTPException(
            status_code=422,
            detail=f"unknown machine '{machine}' (known: {known}, or 'auto')",
        )
    return resolved


async def _forward(
    target: str,
    cmd: DispatchCommand,
    on_behalf_of: str,
    barb_claim: BarbClaim | None = None,
    caller_str: str = "",
) -> dict[str, Any]:
    principal = caller_str or cmd.requested_by
    with barb_write_fence(barb_claim, surface=cmd.surface, target=f"role:{cmd.role}", principal=principal):
        url = staff_fleet.peer_nodes().get(target)
        if url is None:
            raise HTTPException(status_code=422, detail=f"unknown machine '{target}'")
    try:
        body = cmd.forward_body()
        status, data = await staff_fleet.forward_run(url, cmd.role, body, on_behalf_of, barb_claim=barb_claim)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"machine '{target}' unreachable: {exc}") from exc
    if status >= 400:
        raise HTTPException(status_code=status if status < 500 else 502, detail=data.get("detail", data))
    kind = "dry-run" if cmd.dry_run else "dispatch"
    log.info("staff: forwarded %s run for role=%s to %s by %s", kind, cmd.role, target, cmd.requested_by)
    return {**data, "machine": data.get("machine", target), "forwarded_to": target}


def _refuse_if_excluded(cmd: DispatchCommand) -> None:
    """409 when (repo, issue, declared paths) fall in an active dispatch exclusion.

    Runs before any forward/plan/audit so no run row exists for a refused lane. It
    applies to dry runs too, so a preview tells the caller the lane is owned.
    """
    if not cmd.repo:
        return
    from dispatch_routing import extract_declared_paths  # noqa: PLC0415
    from staff.exclusions import ExclusionsList, refusal_detail  # noqa: PLC0415

    hit = ExclusionsList().blocking(cmd.repo, cmd.issue, extract_declared_paths(cmd.prompt))
    if hit is not None:
        log.info("staff: dispatch of %s on %s#%s refused by exclusion %s", cmd.role, cmd.repo, cmd.issue, hit.id)
        raise HTTPException(status_code=409, detail=refusal_detail(hit))


async def dispatch_staff_run(
    cmd: DispatchCommand, caller: Principal, *, barb_claim: BarbClaim | None = None
) -> dict[str, Any]:
    """Dispatch (or preview) one staff run under the shared policy.

    Pre: ``cmd`` is a valid :class:`DispatchCommand`; ``caller`` is the authenticated
    principal the rate limit and audit are charged to.
    Post: a forwarded call returns the peer's response plus ``forwarded_to``; a dry run
    returns the plan and creates nothing; a real local dispatch writes the ``dispatch``
    audit entry, then the ``queued`` run row, then starts the worker (#1796), so an audit
    outage starts nothing. Any refusal is an ``HTTPException`` raised before a run row
    exists, except ``launch_failed``, which names the run it left ``failed``.
    """
    _refuse_if_excluded(cmd)

    if not cmd.dry_run and cmd.issue and cmd.repo:
        attempt_block = claim_guard.issue_attempt_blocker(
            get_runner().store,
            role=cmd.role,
            repo=cmd.repo,
            issue=cmd.issue,
        )
        if attempt_block is not None:
            raise HTTPException(status_code=409, detail=attempt_block.to_detail())

    if not cmd.dry_run and not cmd.force_duplicate and cmd.issue and cmd.repo:
        from staff.preflight_duplicate_guard import check_duplicate_pr_or_run

        await check_duplicate_pr_or_run(
            cmd.repo,
            cmd.issue,
            current_run_id=cmd.run_id(),
            check_open_pr=not cmd.force_claim_override,
            check_claim=not cmd.force_claim_override,
        )

    claim_info = await claim_guard.enforce_claim_guard(cmd, caller)

    if not cmd.dry_run and not cmd.skip_premise_check and (cmd.issue or cmd.prompt):
        from dispatch_premise import close_item_as_resolved_on_main, evaluate_dispatch_premise, log_dispatch_skip
        from dispatch_routing import dispatch_queue_manager, extract_declared_paths, resolve_model_routing

        premise_res = await evaluate_dispatch_premise(
            repository=cmd.repo,
            issue_number=cmd.issue,
            prompt=cmd.prompt,
            check_claim_labels=not claim_guard.enabled(),
            check_open_pr=not (cmd.force_claim_override or cmd.force_duplicate),
        )
        if not premise_res.allowed:
            await log_dispatch_skip(premise_res, cmd.repo, cmd.issue)
            if premise_res.should_close and premise_res.comment and cmd.issue:
                await close_item_as_resolved_on_main(cmd.repo, cmd.issue, premise_res.comment)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "premise_rejected",
                    "reason": premise_res.reason,
                    "message": premise_res.detail,
                    "comment": premise_res.comment,
                },
            )

        if cmd.repo:
            paths = extract_declared_paths(cmd.prompt)
            can_run, blocker = dispatch_queue_manager.can_dispatch(cmd.repo, paths)
            if not can_run:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "queued_overlapping_paths",
                        "reason": f"overlapping paths with active session {blocker} on {cmd.repo}",
                        "message": "waiting for active session PR to merge or close",
                    },
                )

    runner = get_runner()
    spec = runner.roles().get(cmd.role)
    resolved_target: str | None = None
    decision = None
    if spec is not None and cmd.repo and consolidation.threshold(spec) is not None:
        decision = await asyncio.to_thread(consolidation.decide, spec, cmd.repo)  # #1213: gh + capacity I/O

    if cmd.pr and (cmd.auto_fix or cmd.pr_lifecycle == "subscribed"):
        from pr_subscription import evaluate_pr_subscription

        sub_decision = evaluate_pr_subscription(
            is_draft=False,
            operator_opt_in=cmd.operator_opt_in,
            wakeups_count=cmd.wakeups_count,
            max_wakeups=cmd.max_wakeups,
        )
        if not sub_decision.allowed:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "pr_subscription_rejected",
                    "reason": sub_decision.reason,
                    "handoff_to_rd1": sub_decision.handoff_to_rd1,
                },
            )

    # Resolve remote and auto targets after origin-side admission policies but before
    # local planning: only the selected runtime can authoritatively plan its providers.
    if spec is not None and cmd.machine.strip().lower() != "local":
        from staff.provider_failover_config import resolve_failover_chain

        configured_chain = resolve_failover_chain(spec)
        target_provider = cmd.provider or (configured_chain[0].provider if configured_chain else "")
        resolved_target = await _resolve_target(runner, cmd.machine, target_provider, role=spec)
        if resolved_target != "local":
            if cmd.model_tier:
                raise HTTPException(
                    status_code=422,
                    detail="internal model_tier is supported only for local dispatch",
                )
            check_rate_limit("dispatches", caller)
            origin = cmd.origin_node or runner.machine
            obo_hdr = staff_fleet.sign_on_behalf_of(cmd.requested_by, cmd.surface, cmd.thread_id, origin_node=origin)
            return await _forward(resolved_target, cmd, obo_hdr, barb_claim, format_caller(caller))

    req = RunRequest(
        role=cmd.role,
        provider=cmd.provider,
        model=cmd.model,
        model_tier=cmd.model_tier,
        repo=cmd.repo,
        issue=cmd.issue,
        pr=cmd.pr,
        prompt=cmd.prompt,
        machine=cmd.machine,
        requested_by=cmd.requested_by,
        on_behalf_of=cmd.on_behalf_of,
        thread_id=cmd.thread_id,
        work_item_id=cmd.work_item_id,
        consolidation=decision,
        origin_node=cmd.origin_node or (runner.machine if cmd.thread_id else ""),
        pr_lifecycle=cmd.pr_lifecycle,
        head_ref=cmd.head_ref,
        task_type=cmd.task_type,
        standards_extra=cmd.standards_extra,
        requested_skill=cmd.requested_skill,
        execution_mode=cmd.execution_mode or "local",
        dispatch_origin=cmd.dispatch_origin,
    )
    try:
        plan = runner.plan(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from staff.provider_dispatch_preflight import refusal_for_provider_auth  # noqa: PLC0415

    auth_refusal = refusal_for_provider_auth(plan.provider, runner.machine)
    if auth_refusal is not None:
        raise HTTPException(status_code=409, detail=auth_refusal.to_http_detail())
    check_rate_limit("dispatches", caller)
    target = resolved_target or await _resolve_target(runner, cmd.machine, plan.provider, role=spec)
    if target != "local":
        origin = cmd.origin_node or runner.machine
        obo_hdr = staff_fleet.sign_on_behalf_of(cmd.requested_by, cmd.surface, cmd.thread_id, origin_node=origin)
        return await _forward(target, cmd, obo_hdr, barb_claim, format_caller(caller))
    if cmd.dry_run:
        try:
            record_audit(
                action="dispatch",
                target=f"role:{plan.role}",
                principal=format_caller(caller),
                on_behalf_of=cmd.on_behalf_of,
                surface=cmd.surface,
                thread_id=cmd.thread_id,
                outcome="dry_run",
                detail={
                    "repo": plan.repo,
                    "provider": plan.provider,
                    "model": plan.model,
                    "machine": runner.machine,
                    "dry_run": True,
                    **(
                        {"provider_auth_failover": list(plan.provider_auth_skipped)}
                        if plan.provider_auth_skipped
                        else {}
                    ),
                },
                fail_closed=False,
            )
        except AuditError as exc:
            log.warning("staff: dry-run dispatch audit skipped: %s", exc)
        return {"dry_run": True, "plan": plan.to_dict(), "machine": runner.machine, **claim_info}
    check_dispatch_resource_admission(cmd, caller, machine=target)
    budget_ok, budget_reason = BudgetGuard(runner.store).can_dispatch(spec, plan.provider)
    if not budget_ok and not cmd.ignore_budget:
        raise HTTPException(
            status_code=429,
            detail={"code": "budget_exceeded", "message": f"{budget_reason}; resend with ignore_budget to override"},
        )
    run_id = cmd.run_id()
    audit_ctx: dict[str, Any] = {
        "target": f"role:{plan.role}",
        "principal": format_caller(caller),
        "on_behalf_of": cmd.on_behalf_of,
        "surface": cmd.surface,
        "thread_id": cmd.thread_id,
        "request_id": run_id,
        "run_id": run_id,
    }
    from dispatch_routing import resolve_model_routing

    routing = resolve_model_routing(labels=(), prompt=cmd.prompt, requested_model=cmd.model or "")
    try:
        record_audit(
            action="dispatch",
            **audit_ctx,
            outcome="admitted",
            detail={
                "repo": plan.repo,
                "target_ref": plan.target_ref,
                "provider": plan.provider,
                "tier": routing.tier,
                "model": plan.model or routing.model,
                "routing_reason": routing.reason,
                "machine": runner.machine,
                **({"force_duplicate": True} if cmd.force_duplicate else {}),
                **({"operation_id": cmd.operation_id} if cmd.operation_id else {}),
                **({"ignore_budget": True, "budget_reason": budget_reason} if not budget_ok else {}),
                **({"claim_check": claim_info["claim_check"]} if claim_info else {}),
                **({"provider_auth_failover": list(plan.provider_auth_skipped)} if plan.provider_auth_skipped else {}),
            },
            fail_closed=True,
        )
    except AuditError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "audit_unavailable", "message": str(exc), "retryable": True},
        ) from exc
    # Barb-authored dispatch: the lease check and the run admission share one critical section, so a
    # takeover cannot land between them (#2734). ``admit`` is synchronous; never await inside the fence.
    with barb_write_fence(
        barb_claim, surface=cmd.surface, target=f"role:{plan.role}", principal=format_caller(caller), audit=audit_ctx
    ):
        rec, plan, created = runner.admit(req, run_id=run_id)
    auth_skipped = getattr(plan, "provider_auth_skipped", None)
    if created and auth_skipped:
        from staff.failover_dispatch import append_chain_attempt  # noqa: PLC0415

        for row in auth_skipped:
            append_chain_attempt(
                runner.store,
                rec.id,
                provider=str(row.get("provider") or ""),
                model=None,
                outcome=str(row.get("outcome") or "auth_expired"),
            )
    if created and cmd.issue and cmd.repo and claim_info.get("claim_check") != "unavailable":
        applied = await claim_guard.apply_claim(rec.id, cmd.repo, cmd.issue, rec.role)
        if applied:
            runner.store.append_event(rec.id, "claim_label", f"{claim_guard.full_repo(cmd.repo)}#{cmd.issue} {applied}")
    if created:
        from staff.routing_shadow import record_for_run  # noqa: PLC0415

        record_for_run(runner.store, rec)  # shadow advice only (STAFF_ROUTING_MODE=shadow); never reroutes
        try:
            runner.launch(rec, plan)
        except Exception as exc:
            _record_launch_failure(runner, rec.id, exc, audit_ctx)
            raise HTTPException(
                status_code=503,
                detail={"code": "launch_failed", "message": f"worker did not start: {exc}", "run_id": rec.id},
            ) from exc
    log.info(
        "staff: dispatched %s role=%s provider=%s repo=%s target=%s by %s",
        rec.id,
        rec.role,
        rec.provider,
        rec.repo,
        rec.target_ref,
        cmd.requested_by,
    )
    return {"dry_run": False, "run": rec.to_dict(), "machine": runner.machine, **claim_info}


def _record_launch_failure(runner: StaffRunner, run_id: str, exc: Exception, audit_ctx: dict[str, Any]) -> None:
    """Leave an admitted run whose worker never started visibly ``failed`` (#1796)."""
    error = f"worker did not start: {exc}"
    runner.store.update_run(run_id, status="failed", error=error, failure_class="launch_failed", ended_at=_now())
    claim_guard.release_claim(run_id, "failed")
    runner.store.append_event(run_id, "failed", error)
    record_audit(action="dispatch", **audit_ctx, outcome="launch_failed", detail={"error": str(exc)}, fail_closed=False)
