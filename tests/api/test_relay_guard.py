"""Relay guard (#3233, slice 1 of #3226): every postman delivery path passes through this gate.

Pure engine, no network, no dispatch. Each acceptance criterion is one focused test.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta

import pytest
from coordination import relay_guard
from coordination.relay_guard import RelayGuard, RelayRejected

ROSTER = frozenset({"claude", "codex"})
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _fake_pem() -> str:
    # Built at runtime, no literal PEM marker in source (matches tests/unit/test_conversations_store.py).
    prefix, key_type = "-" * 5, "PRIVATE " + "KEY"  # pragma: allowlist secret
    return f"{prefix}BEGIN {key_type}{prefix}\nMIIEowIBAAKCAQEA0{'b' * 30}\n{prefix}END {key_type}{prefix}"


def env(**overrides: object) -> dict[str, object]:
    base = {"id": "m_1", "to": "claude", "body": "hello"}
    base.update(overrides)
    return base


def make_guard(**kwargs: object) -> RelayGuard:
    return RelayGuard(roster=ROSTER, clock=lambda: NOW, **kwargs)


def test_admits_a_well_formed_envelope() -> None:
    guard = make_guard()
    assert guard.admit(env()) == env()


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 36,
        "sk-ant-" + "b" * 24,
        "AKIA" + "C" * 16,
        "Bearer " + "x" * 24,
        _fake_pem(),
    ],
)
def test_refuses_a_secret_without_redacting_or_logging_it(secret: str) -> None:
    guard = make_guard()
    with pytest.raises(RelayRejected):
        guard.admit(env(body=f"here is one: {secret}"))
    assert guard.dead_letters() == []


def test_refuses_an_unknown_recipient() -> None:
    guard = make_guard()
    with pytest.raises(RelayRejected):
        guard.admit(env(to="mystery-agent"))


@pytest.mark.parametrize("recipient", ["claude", "codex", "barb", "rd-barb", "claude-planner", "*"])
def test_admits_roster_and_fleet_recipients(recipient: str) -> None:
    guard = make_guard()
    assert guard.admit(env(id=f"m-{recipient}", to=recipient))["to"] == recipient


def test_refuses_dieter_as_a_relay_recipient() -> None:
    guard = make_guard()
    with pytest.raises(RelayRejected):
        guard.admit(env(to="dieter"))


def test_drops_an_envelope_over_the_hop_limit() -> None:
    guard = make_guard()
    with pytest.raises(RelayRejected):
        guard.admit(env(hops=4))
    assert any(dl.envelope_id == "m_1" for dl in guard.dead_letters())


def test_admits_an_envelope_at_the_hop_limit() -> None:
    guard = make_guard()
    assert guard.admit(env(hops=3))["hops"] == 3


def test_refuses_a_replayed_envelope_id() -> None:
    guard = make_guard()
    guard.admit(env())
    with pytest.raises(RelayRejected):
        guard.admit(env())


def test_replay_dedup_stops_a_three_node_relay_cycle() -> None:
    """A per-edge hop count alone would not catch a cycle A -> B -> C -> A; id dedup does."""
    guard = make_guard()
    message = env(id="m_cycle", hops=0)
    guard.admit(message)
    for hop in (1, 2, 3):
        with pytest.raises(RelayRejected):
            guard.admit(env(id="m_cycle", hops=hop))


def test_stale_envelope_is_dead_lettered_and_listed() -> None:
    guard = make_guard()
    stale = env(expiry=(NOW - timedelta(minutes=1)).isoformat())
    with pytest.raises(RelayRejected):
        guard.admit(stale)
    letters = guard.dead_letters()
    assert len(letters) == 1
    assert letters[0].envelope_id == "m_1"
    assert "expir" in letters[0].reason.lower()


def test_envelope_without_expiry_is_not_stale() -> None:
    guard = make_guard()
    assert guard.admit(env())["id"] == "m_1"


def test_dead_letters_list_is_bounded() -> None:
    guard = make_guard()
    for i in range(relay_guard.MAX_DEAD_LETTERS + 10):
        with pytest.raises(RelayRejected):
            guard.admit(env(id=f"m-stale-{i}", expiry=(NOW - timedelta(minutes=1)).isoformat()))
    assert len(guard.dead_letters()) == relay_guard.MAX_DEAD_LETTERS


def test_seen_ids_are_bounded_so_memory_cannot_grow_without_limit() -> None:
    guard = make_guard()
    for i in range(relay_guard.MAX_SEEN + 10):
        guard.admit(env(id=f"m-{i}"))
    # The earliest ids fell out of the bounded window, so they can be seen again.
    assert guard.admit(env(id="m-0"))["id"] == "m-0"


def test_relay_guard_imports_nothing_from_dispatch_or_staff_run_code() -> None:
    tree = ast.parse(inspect.getsource(relay_guard))
    module_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_names.append(node.module)
    banned = ("dispatch", "staff_run", "cloud_run", "work_request")
    offenders = [n for n in module_names if any(b in n.lower() for b in banned)]
    assert offenders == []
