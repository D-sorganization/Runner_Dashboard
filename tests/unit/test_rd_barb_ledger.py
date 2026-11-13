"""rd-barb decision ledger and confirm-only learned permissions (RD #3066)."""

from __future__ import annotations

import builtins
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from staff.rd_barb_ledger import Ask, DecisionLedger, RuleNotConfirmed, classify, propose
from staff.rd_barb_policy import load_locked_policy

POLICY = load_locked_policy()
REPO = "D-sorganization/Runner_Dashboard"
ASK = Ask(action="arm-auto-merge", repo=REPO, scope="docs-only")
HELD = Ask(action="arm-auto-merge", repo=REPO, scope="#2658 re-arm")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ledger(tmp_path: Path, clock: Clock) -> DecisionLedger:
    return DecisionLedger(tmp_path / "decisions.jsonl", clock=clock)


def _decide(ledger: DecisionLedger, ask: Ask, decision: str, count: int = 1) -> None:
    for i in range(count):
        ledger.record_decision(ask, decision, source_thread_id="th_1", correlation_id=f"c{i}")


def _surface(ledger: DecisionLedger, clock: Clock) -> list[dict]:
    found = propose(ledger.records(), POLICY, clock())
    for envelope in found:
        ledger.record_proposal(envelope)
    return found


def _confirm(ledger: DecisionLedger, proposal: dict, by: str = "dieter") -> None:
    ledger.record_confirm(proposal["correlation_id"], confirmed_by=by, source_thread_id="th_1", correlation_id="cf")


def test_ledger_is_append_only(ledger: DecisionLedger, monkeypatch: pytest.MonkeyPatch) -> None:
    modes: list[str] = []
    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open", lambda f, mode="r", *a, **k: modes.append(mode) or real_open(f, mode, *a, **k)
    )
    _decide(ledger, ASK, "approve")
    first = ledger.path.read_text(encoding="utf-8")
    _decide(ledger, ASK, "deny")
    assert ledger.path.read_text(encoding="utf-8").startswith(first) and len(ledger.records()) == 2
    assert {m for m in modes if set(m) - {"r", "b", "t"}} == {"a"}
    record = ledger.records()[0]
    assert set(record) == {
        "id",
        "ts",
        "kind",
        "action",
        "repo",
        "scope",
        "decision",
        "source_thread_id",
        "correlation_id",
    }
    with pytest.raises(TypeError):
        record["decision"] = "deny"  # type: ignore[index]
    assert not [n for n in dir(ledger) if n.split("_")[0] in {"update", "delete", "remove", "clear", "edit", "pop"}]


@pytest.mark.parametrize("decision", ["maybe", "", "APPROVE!"])
def test_ledger_rejects_bad_decision(ledger: DecisionLedger, decision: str) -> None:
    with pytest.raises(ValueError):
        ledger.record_decision(ASK, decision, source_thread_id="t", correlation_id="c")
    with pytest.raises(ValueError):
        Ask(action="", repo=REPO, scope="x")


def test_proposal_only_after_consistent_decisions(ledger: DecisionLedger, clock: Clock) -> None:
    _decide(ledger, ASK, "approve", 2)
    assert propose(ledger.records(), POLICY, clock()) == []
    _decide(ledger, ASK, "approve")
    [envelope] = propose(ledger.records(), POLICY, clock())
    assert envelope["kind"] == "question" and envelope["rule"]["verdict"] == "auto-ok"
    assert [o["id"] for o in envelope["options"] if o["recommended"]] == [envelope["recommended_option"]]
    assert envelope["expiry"] == (clock() + timedelta(days=14)).isoformat()
    _decide(ledger, ASK, "deny")
    assert propose(ledger.records(), POLICY, clock()) == []  # one contrary decision blocks learning


def test_holds_and_safety_limits_are_unlearnable(ledger: DecisionLedger, clock: Clock) -> None:
    _decide(ledger, HELD, "approve", 100)
    assert propose(ledger.records(), POLICY, clock()) == []
    assert classify(HELD, ledger.records(), POLICY) == "never"
    assert classify(Ask(action="lift the holds", repo=REPO, scope="all"), (), POLICY) == "never"
    forged = {"rule": {"action": HELD.action, "repo": REPO, "scope": HELD.scope}, "correlation_id": "x", "surfacing": 1}
    with pytest.raises(RuleNotConfirmed):
        ledger.record_proposal(forged)


def test_no_rule_without_owner_confirm(ledger: DecisionLedger, clock: Clock) -> None:
    _decide(ledger, ASK, "approve", 3)
    [proposal] = _surface(ledger, clock)
    assert classify(ASK, ledger.records(), POLICY) == "ask-with-recommendation"
    for imposter in ("rd-barb", "grok-barb", ""):
        with pytest.raises(RuleNotConfirmed):
            _confirm(ledger, proposal, by=imposter)
    with pytest.raises(RuleNotConfirmed):
        ledger.record_confirm("rdb-rule-unknown", confirmed_by="dieter", source_thread_id="t", correlation_id="c")
    assert classify(ASK, ledger.records(), POLICY) == "ask-with-recommendation"
    _confirm(ledger, proposal)
    assert classify(ASK, ledger.records(), POLICY) == "auto-ok"


def test_deny_rule_also_needs_confirm_and_classifies_never(ledger: DecisionLedger, clock: Clock) -> None:
    _decide(ledger, ASK, "deny", 3)
    [proposal] = _surface(ledger, clock)
    assert classify(ASK, ledger.records(), POLICY) == "ask-with-recommendation"
    _confirm(ledger, proposal)
    assert classify(ASK, ledger.records(), POLICY) == "never"


def test_exact_match_only(ledger: DecisionLedger, clock: Clock) -> None:
    _decide(ledger, ASK, "approve", 3)
    _confirm(ledger, _surface(ledger, clock)[0])
    for other in (Ask(ASK.action, "D-sorganization/Other", ASK.scope), Ask(ASK.action, REPO, "docs-only/sub")):
        assert classify(other, ledger.records(), POLICY) == "ask-with-recommendation"


def test_proposal_expires_after_14_days_and_resurfaces_exactly_once(ledger: DecisionLedger, clock: Clock) -> None:
    _decide(ledger, ASK, "approve", 3)
    [first] = _surface(ledger, clock)
    clock.now += timedelta(days=13)
    assert _surface(ledger, clock) == []
    clock.now += timedelta(days=2)
    [again] = _surface(ledger, clock)
    assert again["correlation_id"] == first["correlation_id"] and again["surfacing"] == 2
    for days in (15, 30, 365):
        clock.now += timedelta(days=days)
        assert _surface(ledger, clock) == []


def test_classifier_is_pure_no_network(ledger: DecisionLedger, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    _decide(ledger, ASK, "approve", 3)
    records = ledger.records()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network call"))
    assert classify(ASK, records, POLICY) == "ask-with-recommendation"
    assert classify(ASK, records, POLICY) == classify(ASK, records, POLICY)
    assert len(propose(records, POLICY, clock())) == 1 and ledger.records() == records
