"""Decision SLA on owner inbox items (WP-2.6, #1607).

- A proposal may carry ``decide_by`` and ``default_if_silent``; its source sets them at creation.
- Barb's follow-up sweep applies the default once ``decide_by`` passes: ``deny`` is written back
  to the proposal store, ``approve`` runs through the action executor, and both are audited.
- A proposal past ``decide_by`` without a default is only pinged in Barb's thread, once per interval.
- ``approve`` by silence is limited to low-risk actions and re-checked at sweep time.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fleet_events import get_event_store
from staff.actions import ACTION_REGISTRY, ActionDefinition, ActionResult
from staff.audit import get_audit_store, reset_audit_store
from staff.conversations import ConversationStore, get_conversation_store, reset_conversation_store
from staff.decision_sla import apply_decision_defaults
from staff.followup import FollowupEngine, reset_followup_engine
from staff.inbox import _collect_approvals
from staff.store import get_run_store, reset_store
from staff.work_items import get_work_item_store, reset_work_item_store

CALLS: list[dict[str, Any]] = []


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _noop(params: dict[str, Any], ctx: object) -> ActionResult:
    CALLS.append(params)
    return ActionResult(success=True, result={"ok": True})


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ConversationStore]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff.sqlite3"))
    monkeypatch.setenv("RUNNER_DASHBOARD_PUSH_DB", str(tmp_path / "push.sqlite3"))
    reset_store()
    reset_work_item_store()
    reset_conversation_store()
    reset_followup_engine()
    reset_audit_store()
    CALLS.clear()
    for name, risk in (("test.low", "low"), ("test.medium", "medium")):
        monkeypatch.setitem(
            ACTION_REGISTRY._actions,  # noqa: SLF001 - test-only registration
            name,
            ActionDefinition(name=name, description=name, required_scope="staff.read", risk_class=risk, executor=_noop),
        )
    s = get_conversation_store()
    yield s
    reset_followup_engine()
    reset_conversation_store()
    reset_work_item_store()
    reset_store()
    reset_audit_store()


def _propose(s: ConversationStore, action: str = "test.low", **sla: Any) -> str:
    th = s.create_thread(title="SLA", kind="direct", participants=["planner", "user"])
    msg = s.add_message(thread_id=th.id, author_kind="user", author="user", body_md="proposal")
    risk = ACTION_REGISTRY.get(action).risk_class  # type: ignore[union-attr]
    return s.create_proposal(message_id=msg.id, thread_id=th.id, action=action, risk=risk, **sla).id


def _audits(action: str) -> list[Any]:
    return [e for e in get_audit_store().list_entries(limit=200) if e.action == action]


# ── the contract at creation ────────────────────────────────────────────────
@pytest.mark.unit
def test_proposal_round_trips_its_sla(store: ConversationStore) -> None:
    decide_by = _iso(datetime.now(UTC) + timedelta(hours=2))
    pid = _propose(store, decide_by=decide_by, default_if_silent="deny")
    prop = store.get_proposal(pid)
    assert prop is not None
    assert (prop.decide_by, prop.default_if_silent) == (decide_by, "deny")
    assert prop.to_dict()["decide_by"] == decide_by and prop.to_dict()["default_if_silent"] == "deny"


@pytest.mark.unit
def test_proposal_without_sla_has_none(store: ConversationStore) -> None:
    prop = store.get_proposal(_propose(store))
    assert prop is not None and (prop.decide_by, prop.default_if_silent) == (None, "")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sla", "match"),
    [
        ({"default_if_silent": "deny"}, "decide_by"),
        ({"decide_by": "tomorrow", "default_if_silent": "deny"}, "ISO"),
        (
            {"decide_by": _iso(datetime.now(UTC) + timedelta(hours=1)), "default_if_silent": "maybe"},
            "default_if_silent",
        ),
        ({"decide_by": _iso(datetime.now(UTC) + timedelta(hours=30))}, "expire"),
    ],
)
def test_create_rejects_an_incoherent_sla(store: ConversationStore, sla: dict[str, str], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _propose(store, **sla)


@pytest.mark.unit
def test_approve_by_silence_is_refused_for_a_non_low_risk_action(store: ConversationStore) -> None:
    decide_by = _iso(datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(ValueError, match="low-risk"):
        _propose(store, "test.medium", decide_by=decide_by, default_if_silent="approve")


@pytest.mark.unit
def test_inbox_approval_item_carries_the_sla(store: ConversationStore) -> None:
    decide_by = _iso(datetime.now(UTC) + timedelta(hours=1))
    _propose(store, decide_by=decide_by, default_if_silent="deny")
    [item] = _collect_approvals(store)
    assert (item.decide_by, item.default_if_silent) == (decide_by, "deny")
    assert item.to_dict()["decide_by"] == decide_by


# ── the sweep ────────────────────────────────────────────────────────────────
def _due(store: ConversationStore, action: str = "test.low", default: str = "") -> str:
    """A proposal whose ``decide_by`` passed a minute ago."""
    decide_by = _iso(datetime.now(UTC) + timedelta(minutes=1))
    pid = _propose(store, action, decide_by=decide_by, default_if_silent=default)
    return pid


def _after(pid: str, store: ConversationStore) -> datetime:
    prop = store.get_proposal(pid)
    assert prop is not None and prop.decide_by
    return datetime.fromisoformat(prop.decide_by.replace("Z", "+00:00")) + timedelta(minutes=1)


@pytest.mark.unit
def test_default_deny_is_written_back_and_audited(store: ConversationStore) -> None:
    pid = _due(store, default="deny")
    [outcome] = apply_decision_defaults(store, now=_after(pid, store))
    assert (outcome.proposal_id, outcome.applied, outcome.outcome) == (pid, "deny", "denied")
    prop = store.get_proposal(pid)
    assert prop is not None and prop.state == "denied" and prop.decided_by == "barb"
    [entry] = _audits("decision_default_applied")
    assert entry.target == pid and entry.detail["default_if_silent"] == "deny"


@pytest.mark.unit
def test_default_approve_runs_the_action_through_the_executor(store: ConversationStore) -> None:
    pid = _due(store, default="approve")
    [outcome] = apply_decision_defaults(store, now=_after(pid, store))
    assert outcome.outcome == "executed"
    assert len(CALLS) == 1
    prop = store.get_proposal(pid)
    assert prop is not None and prop.state == "done"
    assert _audits("decision_default_applied")[0].detail["outcome"] == "executed"


@pytest.mark.unit
def test_default_approve_is_refused_if_the_action_became_riskier(
    store: ConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = _due(store, default="approve")
    monkeypatch.setitem(
        ACTION_REGISTRY._actions,  # noqa: SLF001
        "test.low",
        ActionDefinition(
            name="test.low", description="x", required_scope="staff.read", risk_class="high", executor=_noop
        ),
    )
    [outcome] = apply_decision_defaults(store, now=_after(pid, store))
    assert outcome.outcome == "refused"
    assert CALLS == []
    prop = store.get_proposal(pid)
    assert prop is not None and prop.state == "proposed"
    assert _audits("decision_default_refused")


@pytest.mark.unit
def test_items_before_decide_by_or_without_default_are_left_alone(store: ConversationStore) -> None:
    early = _due(store, default="deny")
    silent = _due(store)
    before = datetime.now(UTC)
    assert apply_decision_defaults(store, now=before) == []
    later = _after(early, store)
    assert [o.proposal_id for o in apply_decision_defaults(store, now=later)] == [early]
    prop = store.get_proposal(silent)
    assert prop is not None and prop.state == "proposed"


@pytest.mark.unit
def test_sweep_applies_defaults_and_pings_silent_items_once(store: ConversationStore) -> None:
    engine = FollowupEngine(
        run_store=get_run_store(),
        work_item_store=get_work_item_store(),
        conversation_store=store,
        event_store=get_event_store(),
        sweep_interval_seconds=300,
    )
    defaulted = _due(store, default="deny")
    silent = _due(store)
    now = _after(silent, store)

    result = engine.sweep(now=now)
    kinds = {(r.target_id, r.action_taken) for r in result.followups}
    assert (defaulted, "default_applied") in kinds and (silent, "decision_ping") in kinds
    assert all(r.target_kind == "proposal" for r in result.followups if r.target_id in {defaulted, silent})

    again = engine.sweep(now=now + timedelta(seconds=10))
    assert [r for r in again.followups if r.target_id in {defaulted, silent}] == []
    barb = next(t for t in store.list_threads(status="open") if t.title == "Barb")
    assert sum(silent in m.body_md for m in store.list_messages(barb.id)) == 1
