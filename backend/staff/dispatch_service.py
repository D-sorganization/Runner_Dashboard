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
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import HTTPException
from identity import Principal, format_caller
from staff import consolidation
from staff import fleet as staff_fleet
from staff.audit import record_audit
from staff.rate_limit import check_rate_limit
from staff.runner import RunRequest, StaffRunner, get_runner

log = logging.getLogger("dashboard.staff")

_FAILURE_CLASS_BY_STATUS = {
    403: "permission_denied",
    422: "invalid_params",
    429: "rate_limited",
    502: "upstream_error",
    503: "peer_unreachable",
}


@dataclass(frozen=True)
class DispatchCommand:
    """A validated dispatch request, independent of the surface it came from.

    ``requested_by`` / ``on_behalf_of`` / ``surface`` / ``thread_id`` are provenance;
    the rest mirrors the /run body.
    """

    role: str
    requested_by: str
    provider: str | None = None
    model: str | None = None
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
        return body


def failure_class_for(exc: HTTPException) -> str:
    """Map a dispatch refusal onto the action failure taxonomy."""
    return _FAILURE_CLASS_BY_STATUS.get(exc.status_code, "dispatch_failed")


def refusal_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or detail)
    return str(detail)


async def _resolve_target(runner: StaffRunner, machine: str, provider: str) -> str:
    """Machine targeting (#1197): ``local`` / this host → local; ``auto`` → least loaded
    online node with the provider installed; a peer name → that peer; unknown → 422."""
    peers = staff_fleet.peer_nodes()
    if machine.strip().lower() == "auto":
        board_view = await staff_fleet.aggregate_board(staff_fleet.local_board(runner), peers)
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


async def _forward(target: str, cmd: DispatchCommand, on_behalf_of: str) -> dict[str, Any]:
    url = staff_fleet.peer_nodes().get(target)
    if url is None:
        raise HTTPException(status_code=422, detail=f"unknown machine '{target}'")
    try:
        status, data = await staff_fleet.forward_run(url, cmd.role, cmd.forward_body(), on_behalf_of=on_behalf_of)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"machine '{target}' unreachable: {exc}") from exc
    if status >= 400:
        raise HTTPException(status_code=status if status < 500 else 502, detail=data.get("detail", data))
    log.info(
        "staff: forwarded %s run for role=%s to %s by %s",
        "dry-run" if cmd.dry_run else "dispatch",
        cmd.role,
        target,
        cmd.requested_by,
    )
    return {**data, "machine": data.get("machine", target), "forwarded_to": target}


async def dispatch_staff_run(cmd: DispatchCommand, caller: Principal) -> dict[str, Any]:
    """Dispatch (or preview) one staff run under the shared policy.

    Pre: ``cmd`` is a valid :class:`DispatchCommand`; ``caller`` is the authenticated
    principal the rate limit and audit are charged to.
    Post: a forwarded call returns the peer's response plus ``forwarded_to``; a dry run
    returns the plan and creates nothing; a real local dispatch has a ``queued`` run row
    and a ``dispatch`` audit entry before returning. Any refusal is an ``HTTPException``
    raised before a run row exists.
    """
    runner = get_runner()
    spec = runner.roles().get(cmd.role)
    decision = None
    if spec is not None and cmd.repo and consolidation.threshold(spec) is not None:
        decision = await asyncio.to_thread(consolidation.decide, spec, cmd.repo)  # #1213: gh + capacity I/O

    req = RunRequest(
        role=cmd.role,
        provider=cmd.provider,
        model=cmd.model,
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
    )
    try:
        plan = runner.plan(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    check_rate_limit("dispatches", caller)
    target = await _resolve_target(runner, cmd.machine, plan.provider)
    if target != "local":
        obo_hdr = staff_fleet.sign_on_behalf_of(cmd.requested_by, cmd.surface, cmd.thread_id)
        return await _forward(target, cmd, obo_hdr)
    if cmd.dry_run:
        return {"dry_run": True, "plan": plan.to_dict(), "machine": runner.machine}
    rec = runner.submit(req)
    record_audit(
        action="dispatch",
        target=f"role:{rec.role}",
        principal=format_caller(caller),
        on_behalf_of=cmd.on_behalf_of,
        surface=cmd.surface,
        thread_id=cmd.thread_id,
        request_id=rec.id,
        run_id=rec.id,
        outcome="success",
        detail={"repo": rec.repo, "target_ref": rec.target_ref, "provider": rec.provider, "machine": rec.machine},
        fail_closed=True,
    )
    log.info(
        "staff: dispatched %s role=%s provider=%s repo=%s target=%s by %s",
        rec.id,
        rec.role,
        rec.provider,
        rec.repo,
        rec.target_ref,
        cmd.requested_by,
    )
    return {"dry_run": False, "run": rec.to_dict(), "machine": runner.machine}
