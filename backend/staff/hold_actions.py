"""Staff hold / unhold action executors and verifiers (split from action_executors, #1313)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from identity import format_caller
from staff.audit import record_audit

if TYPE_CHECKING:
    from staff.actions import ActionContext, ActionResult


def execute_staff_hold(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    text = str(params.get("text") or "").strip()
    if not text:
        return ActionResult(success=False, error="Missing 'text' for hold", failure_class="invalid_params")
    raw_applies = params.get("applies_to") or ["*"]
    applies_to = [str(a) for a in raw_applies] if isinstance(raw_applies, list) else [str(raw_applies)]
    from staff.holds import Hold, HoldsList, _hold_id

    hl = HoldsList()
    holds = hl.load()
    hid = str(params.get("hold_id") or _hold_id(text))
    existing = next((h for h in holds if h.id == hid or h.text.lower() == text.lower()), None)
    if existing:
        existing.active = True
        existing.applies_to = list(set(existing.applies_to + applies_to))
    else:
        holds.append(
            Hold(
                id=hid,
                text=text,
                applies_to=applies_to,
                lifted_when=str(params.get("lifted_when") or ""),
                active=True,
                kind="schedule",  # a staff.hold action always blocks scheduling (#1726)
            )
        )
    hl.replace([h.to_dict() for h in holds])
    record_audit(
        action="hold_set",
        target=f"hold:{hid}",
        principal=format_caller(ctx.caller) if ctx.caller else "staff_action",
        surface="thread",
        thread_id=ctx.thread_id,
        outcome="success",
        detail={"text": text, "applies_to": applies_to},
        fail_closed=True,
        store=ctx.audit_store,
    )
    return ActionResult(success=True, result={"hold_id": hid, "text": text, "active": True})


def verify_staff_hold(res: ActionResult, params: dict[str, Any], ctx: ActionContext) -> tuple[bool, str]:
    from staff.holds import HoldsList

    text = str(params.get("text") or "").strip().lower()
    holds = HoldsList().load()
    if any(h.active and (h.text.lower() == text or (res.result and h.id == res.result.get("hold_id"))) for h in holds):
        return True, "Hold verified active in ledger"
    return False, "Hold not found or inactive"


def execute_staff_unhold(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    hid = str(params.get("hold_id") or "").strip()
    text = str(params.get("text") or "").strip().lower()
    if not hid and not text:
        return ActionResult(success=False, error="Must specify 'hold_id' or 'text'", failure_class="invalid_params")
    from staff.holds import HoldsList

    hl = HoldsList()
    holds = hl.load()
    matched = False
    for h in holds:
        if (hid and h.id == hid) or (text and h.text.lower() == text):
            h.active = False
            matched = True
    if not matched:
        return ActionResult(success=False, error="Hold not found", failure_class="not_found")
    hl.replace([h.to_dict() for h in holds])
    record_audit(
        action="hold_lift",
        target=f"hold:{hid or text}",
        principal=format_caller(ctx.caller) if ctx.caller else "staff_action",
        surface="thread",
        thread_id=ctx.thread_id,
        outcome="success",
        detail={"hold_id": hid, "text": text},
        fail_closed=True,
        store=ctx.audit_store,
    )
    return ActionResult(success=True, result={"hold_id": hid, "lifted": True})


def verify_staff_unhold(res: ActionResult, params: dict[str, Any], ctx: ActionContext) -> tuple[bool, str]:
    from staff.holds import HoldsList

    hid = str(params.get("hold_id") or "").strip()
    text = str(params.get("text") or "").strip().lower()
    for h in HoldsList().load():
        if (hid and h.id == hid) or (text and h.text.lower() == text):
            if h.active:
                return False, "Hold still active"
    return True, "Hold verified lifted"
