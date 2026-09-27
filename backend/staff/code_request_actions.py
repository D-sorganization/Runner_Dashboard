"""Code Request action executors for Staff Console (split from action_executors, #1313, #1604)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from identity import format_caller
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from code_requests.store import CodeRequestStore
    from staff.actions import ActionContext, ActionResult


def execute_code_request_create(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.action_executors import CODE_REQUEST_OWNER_ROLE
    from staff.actions import ActionResult

    title = str(params.get("title") or "").strip()
    repo = str(params.get("repo") or "").strip()
    if not title or not repo:
        return ActionResult(success=False, error="Missing 'title' or 'repo'", failure_class="invalid_params")
    from staff.work_items import get_work_item_store

    wi_store = get_work_item_store()
    role = str(params.get("role") or CODE_REQUEST_OWNER_ROLE)
    wi = wi_store.create_work_item(
        title=f"[Code Request] {title}",
        owner_role=role,
        thread_id=ctx.thread_id,
        requested_by=format_caller(ctx.caller) if ctx.caller else "staff_action",
    )
    return ActionResult(success=True, result={"work_item_id": wi.id, "title": title, "repo": repo})


class CodeRequestUpdateParams(BaseModel):
    """Parameters for code_request.update action.

    Preconditions:
    - id: identifier of the Code Request
    - description: updated prompt/PRD description
    - extra fields: forbidden
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str


def execute_code_request_update(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    """Update a Code Request's prompt/PRD description while in draft or triage state.

    Preconditions:
    - params must contain exactly 'id' and 'description' (extra fields forbidden).
    - Code Request with specified 'id' must exist.
    - Code Request must be in 'draft' or 'triage' state.
    Postconditions:
    - Code Request's prompt is updated and persisted via CodeRequestStore.save.
    - Returns ActionResult indicating success or failure with appropriate failure_class.
    """
    from code_requests.store import get_code_request_store
    from pydantic import ValidationError
    from staff.actions import ActionResult
    from staff.loop_bridge import BridgeUnavailableError, run_on_loop

    try:
        parsed = CodeRequestUpdateParams.model_validate(params)
    except ValidationError as exc:
        return ActionResult(success=False, error=str(exc), failure_class="invalid_params")
    try:
        outcome = run_on_loop(_update_code_request_prompt, get_code_request_store(), parsed)
    except BridgeUnavailableError as exc:
        return ActionResult(success=False, error=f"code_request.update {exc}", failure_class="bridge_unavailable")
    if isinstance(outcome, str):
        failure_class, _, error = outcome.partition(":")
        return ActionResult(success=False, error=error, failure_class=failure_class)
    return ActionResult(success=True, result=outcome)


async def _update_code_request_prompt(store: CodeRequestStore, parsed: CodeRequestUpdateParams) -> dict[str, Any] | str:
    """Load, check and save on the server loop; return the result or ``"<failure_class>:<error>"``."""
    from datetime import UTC, datetime

    from code_requests.model import CodeRequestState

    req = await store.get(parsed.id)
    if req is None:
        return f"not_found:Code Request '{parsed.id}' not found"
    if req.state not in (CodeRequestState.DRAFT, CodeRequestState.TRIAGE):
        return f"invalid_state:Cannot update Code Request in state '{req.state.value}' (must be 'draft' or 'triage')"
    updated = req.model_copy(update={"prompt": parsed.description, "updated_at": datetime.now(UTC).isoformat()})
    saved = await store.save(updated)
    return {"id": saved.id, "prompt": saved.prompt, "state": saved.state.value}
