"""Postman relay guard (#3233): refuse-on-secret, allowlist, hop≤3, envelope-id dedup, stale TTL, no dispatch."""

from __future__ import annotations

import ast
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from coordination.postman import Delivery, Postman, PostmanState
from coordination.relay_guard import EXTRA_RELAY_AGENTS, MAX_HOPS, RelayGuard, RelayVerdict

from scripts import rd_threads_envelope as envelope
from tests.api.test_postman import FakeBoard, FakeBus, ROSTER, Source, make, msg

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
ROSTER_TUPLE = tuple(ROSTER)


def _env(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "m_relay0001",
        "kind": "update",
        "from": "claude",
        "to": "codex",
        "summary": "relay ping",
        "body": "",
        "hops": 0,
    }
    return envelope.with_defaults({**base, **over})


def guard(*, roster: tuple[str, ...] = ROSTER_TUPLE) -> RelayGuard:
    return RelayGuard(roster, clock=lambda: NOW)


@pytest.mark.parametrize(
    "body",
    [
        "token ghp_abcdefghijklmnopqrstuvwxyz1234567890AB",
        "Bearer abcdefghijklmnopqrstuvwxyz1234567890",
        "key sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "host 10.0.0.42 is down",
        "password=SuperSecretValue",
    ],
)
def test_admit_refuses_envelopes_that_carry_secrets(body: str) -> None:
    verdict = guard().admit(_env(body=body))
    assert verdict == RelayVerdict(False, "secret")


def test_admit_refuses_unknown_recipient() -> None:
    verdict = guard().admit(_env(to="not-a-real-agent"))
    assert verdict == RelayVerdict(False, "unknown_recipient")


def test_admit_refuses_unknown_sender() -> None:
    verdict = guard().admit(_env(**{"from": "mystery-sender"}))
    assert verdict == RelayVerdict(False, "unknown_sender")


def test_admit_refuses_dieter_as_relay_target() -> None:
    verdict = guard().admit(_env(to="dieter"))
    assert verdict == RelayVerdict(False, "dieter_recipient")


def test_admit_allows_roster_plus_extra_relay_agents() -> None:
    g = guard()
    for idx, target in enumerate(("barb", "rd-barb", "claude-planner", "*")):
        assert g.admit(_env(id=f"m_extra{idx}", to=target)).allowed is True
    assert EXTRA_RELAY_AGENTS == frozenset({"barb", "rd-barb", "claude-planner", "*"})


def test_admit_drops_hop_four() -> None:
    verdict = guard().admit(_env(hops=MAX_HOPS + 1))
    assert verdict == RelayVerdict(False, "hop_limit")


def test_admit_refuses_replayed_envelope_id() -> None:
    g = guard()
    first = g.admit(_env())
    assert first.allowed is True
    replay = g.admit(_env(body="still fine"))
    assert replay == RelayVerdict(False, "replay")


def test_envelope_id_dedup_stops_three_node_cycle() -> None:
    g = guard()
    cycle_id = "m_cycle_claude_codex_agy"
    assert g.admit(_env(id=cycle_id, **{"from": "claude"}, to="codex", hops=0)).allowed
    assert g.admit(_env(id=cycle_id, **{"from": "codex"}, to="antigravity", hops=1)) == RelayVerdict(
        False, "replay"
    )
    assert g.admit(_env(id=cycle_id, **{"from": "antigravity"}, to="claude", hops=2)) == RelayVerdict(
        False, "replay"
    )


def test_stale_envelope_dead_letters_and_lists() -> None:
    g = guard()
    expired = (NOW - timedelta(minutes=5)).isoformat()
    verdict = g.admit(_env(expiry=expired))
    assert verdict == RelayVerdict(False, "stale")
    dead = g.dead_letters()
    assert len(dead) == 1 and dead[0].envelope_id == "m_relay0001" and dead[0].reason == "stale"


def test_refusal_never_logs_secret_material(caplog: pytest.LogCaptureFixture) -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz1234567890AB"
    with caplog.at_level(logging.WARNING):
        guard().admit(_env(body=secret))
    assert secret not in caplog.text


def test_import_boundary_has_no_dispatch_or_staff_run_deps() -> None:
    path = Path(__file__).resolve().parents[2] / "backend" / "coordination" / "relay_guard.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned = ("dispatch", "staff.runner", "staff.run_link", "staff.dispatch")
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    for name in imports:
        for token in banned:
            assert token not in name, f"forbidden import {name!r}"


def test_postman_deliver_routes_through_guard_for_secrets() -> None:
    board, bus = FakeBoard(), FakeBus()
    board.live("lane-x", "claude", 100)
    board.inboxes["lane-x"] = [
        msg("m1", "lane-y", "lane-x", "leak ghp_abcdefghijklmnopqrstuvwxyz1234567890AB"),
    ]
    report = make(board, bus).cycle()
    assert bus.calls == 0
    assert any(a.kind == "error" for a in report.actions)
    verdict = guard().check_delivery(
        Delivery(
            to="lane-x",
            kind="message",
            type="request",
            body="ghp_abcdefghijklmnopqrstuvwxyz1234567890AB",
            board_message_id="m1",
            idempotency_key="k" * 8,
        )
    )
    assert verdict == RelayVerdict(False, "secret")


def test_postman_deliver_still_delivers_clean_messages() -> None:
    board, bus = FakeBoard(), FakeBus()
    board.live("lane-x", "claude", 100)
    board.inboxes["lane-x"] = [msg("m1", "lane-y", "lane-x", "ping")]
    make(board, bus).cycle()
    assert bus.calls >= 1
