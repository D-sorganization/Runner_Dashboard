"""Approval-authority preview and parameter-hash revalidation (#1807).

The preview (``can_approve`` / ``denial_reason``) and execution share ONE evaluator, and
the reviewed parameter hash is re-verified before any state change.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from identity import Principal
from staff.actions import (
    ACTION_REGISTRY,
    ActionError,
    ParamsHashMismatchError,
    ParamsHashRequiredError,
    check_approval_policy,
    execute_proposal,
    stored_params_hash,
)
from staff.approval_authority import ApprovalAuthority, evaluate_approval_authority, params_hash
from staff.audit import reset_audit_store
from staff.conversation_models import ActionProposalRecord
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.proposal_preview import build_proposal_preview
from staff.runner import StaffRunner, reset_runner
from staff.store import reset_store

UTC = getattr(_dt, "UTC", _dt.UTC)

USER = Principal(id="u", type="human", name="U", roles=["developer"], scopes=["staff.read", "staff.chat"])
APPROVER = Principal(id="a", type="human", name="A", roles=["operator"], scopes=["staff.read", "staff.approve"])
OWNER = Principal(
    id="o", type="human", name="O", roles=["owner", "admin"], scopes=["staff.read", "staff.approve", "owner"]
)
NO_DISPATCH_SCOPE = Principal(id="n", type="human", name="N", roles=["x"], scopes=["staff.approve"])


@pytest.fixture(autouse=True)
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "authority.sqlite3"))
    monkeypatch.setattr(StaffRunner, "_worker", lambda *args, **kwargs: None)
    for reset in (reset_runner, reset_store, reset_conversation_store, reset_audit_store):
        reset()
    yield
    for reset in (reset_runner, reset_store, reset_conversation_store, reset_audit_store):
        reset()


def _proposal(action: str = "staff.dispatch", risk: str = "medium", **params: Any) -> ActionProposalRecord:
    store = get_conversation_store()
    th = store.create_thread(title="T", kind="direct", participants=["barb", "user"])
    return store.create_proposal(
        message_id="m1",
        thread_id=th.id,
        action=action,
        params=params or {"role": "librarian", "repo": "Tools"},
        risk=risk,
        principal="barb",
    )


# -- params_hash --------------------------------------------------------------------


def test_params_hash_is_key_order_independent_and_hex() -> None:
    a = params_hash("staff.dispatch", {"repo": "Tools", "n": 1})
    b = params_hash("staff.dispatch", {"n": 1, "repo": "Tools"})
    assert a == b
    assert len(a) == 64 and int(a, 16) >= 0


def test_params_hash_changes_with_action_or_params() -> None:
    base = params_hash("staff.dispatch", {"repo": "Tools"})
    assert params_hash("staff.hold", {"repo": "Tools"}) != base
    assert params_hash("staff.dispatch", {"repo": "Tools2"}) != base


def test_params_hash_is_unicode_stable() -> None:
    assert params_hash("a", {"k": "é"}) == params_hash("a", {"k": "é"})
    assert params_hash("a", {"k": "é"}) != params_hash("a", {"k": "e"})


# -- evaluator parity with check_approval_policy -----------------------------------


@pytest.mark.parametrize("who", [USER, APPROVER, OWNER, NO_DISPATCH_SCOPE])
@pytest.mark.parametrize(
    ("action", "risk"), [("staff.review_pr", "low"), ("staff.dispatch", "medium"), ("staff.hold", "high")]
)
def test_evaluator_matches_check_approval_policy(who: Principal, action: str, risk: str) -> None:
    prop = _proposal(action=action, risk=risk)
    action_def = ACTION_REGISTRY.get(action)
    verdict = evaluate_approval_authority(action_def, prop, who, include_state=False)
    try:
        check_approval_policy(action_def, prop, who)
        raised = None
    except PermissionError as exc:
        raised = str(exc)
    assert verdict.allowed == (raised is None)
    assert verdict.reason == (raised or "")


def test_denial_reason_is_non_empty_iff_denied() -> None:
    prop = _proposal()
    action = ACTION_REGISTRY.get("staff.dispatch")
    allowed = evaluate_approval_authority(action, prop, APPROVER)
    denied = evaluate_approval_authority(action, prop, USER)
    assert allowed == ApprovalAuthority(allowed=True, reason="", code="ok")
    assert not denied.allowed and denied.reason and denied.code == "scope_missing"


def test_evaluator_denies_terminal_states_when_state_is_checked() -> None:
    prop = _proposal()
    get_conversation_store().decide_proposal(prop.id, "denied", decided_by="x")
    done = get_conversation_store().get_proposal(prop.id)
    assert done is not None
    verdict = evaluate_approval_authority(ACTION_REGISTRY.get("staff.dispatch"), done, OWNER)
    assert not verdict.allowed and verdict.code == "terminal" and "denied" in verdict.reason


def test_evaluator_flags_expired_proposal() -> None:
    prop = _proposal()
    stale = ActionProposalRecord(
        id=prop.id, message_id="m", action=prop.action, params={}, created_at="2000-01-01T00:00:00+00:00"
    )
    verdict = evaluate_approval_authority(ACTION_REGISTRY.get("staff.dispatch"), stale, OWNER)
    assert verdict.code == "expired" and not verdict.allowed


# -- preview ------------------------------------------------------------------------


def test_preview_fields_for_allowed_and_denied_callers() -> None:
    prop = _proposal()
    store = get_conversation_store()
    ok = build_proposal_preview(store, prop, APPROVER)
    assert ok["can_approve"] is True and ok["denial_reason"] is None
    assert ok["params_hash"] == params_hash(prop.action, prop.params)
    no = build_proposal_preview(store, prop, USER)
    assert no["can_approve"] is False and no["denial_reason"]


def test_preview_is_read_only() -> None:
    prop = _proposal()
    store = get_conversation_store()
    build_proposal_preview(store, prop, OWNER)
    after = store.get_proposal(prop.id)
    assert after is not None and after.state == "proposed" and after.decided_by == ""


def test_preview_denies_when_proposing_role_is_not_permitted() -> None:
    store = get_conversation_store()
    th = store.create_thread(title="T", kind="direct", participants=["barb", "user"])
    msg = store.add_message(thread_id=th.id, author_kind="role", author="no_such_role", kind="text", body_md="x")
    prop = store.create_proposal(
        message_id=msg.id, thread_id=th.id, action="staff.dispatch", params={"repo": "Tools"}, risk="medium"
    )
    verdict = build_proposal_preview(store, prop, OWNER)
    assert verdict["can_approve"] is False and "no_such_role" in verdict["denial_reason"]


# -- execution revalidation ---------------------------------------------------------


def test_execute_with_stale_hash_changes_nothing() -> None:
    prop = _proposal()
    store = get_conversation_store()
    with pytest.raises(ParamsHashMismatchError):
        execute_proposal(prop.id, approver=OWNER, store=store, approve=True, params_hash="0" * 64)
    after = store.get_proposal(prop.id)
    assert after is not None and after.state == "proposed"


def test_execute_without_hash_is_refused() -> None:
    prop = _proposal()
    with pytest.raises(ParamsHashRequiredError):
        execute_proposal(prop.id, approver=OWNER, store=get_conversation_store(), approve=True, params_hash="")
    assert issubclass(ParamsHashRequiredError, ActionError)


def test_execute_with_reviewed_hash_runs() -> None:
    prop = _proposal()
    store = get_conversation_store()
    h = stored_params_hash(store, prop.id)
    assert h == params_hash(prop.action, prop.params)
    res = execute_proposal(prop.id, approver=OWNER, store=store, approve=True, params_hash=h)
    assert res is not None
    after = store.get_proposal(prop.id)
    assert after is not None and after.state != "proposed"
