"""Real ports for the postman engine (issue #2940): RmBoard, AgentBus, ConfigBoardSource, state file,
and the dry-run/--live entry point. ``coordination.postman`` itself (the engine) is covered by
``test_postman.py``; these tests cover only the adapters and the wiring in ``postman_run``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agent_bus.store import AgentBusStore
from coordination.postman import BoardRef, Delivery, Postman, PostmanState
from coordination.postman_adapters import AgentBus, ConfigError, RmBoard
from coordination.postman_adapters import ConfigBoardSource as _ConfigBoardSource
from coordination.postman_run import build_postman, run

from tests.api.test_postman import FakeBoard, FakeBus, Source

# -- AgentBus ------------------------------------------------------------------------------------


def _delivery(to: str = "claude", mid: str = "m1", key: str = "k" * 8, work_number: int = 2376) -> Delivery:
    return Delivery(
        to=to,
        kind="message",
        type="request",
        body="hello",
        board_message_id=mid,
        idempotency_key=key,
        work_number=work_number,
    )


@pytest.fixture
def bus_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentBusStore:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    return AgentBusStore(db_path=tmp_path / "agent_bus.sqlite3")


def _register_recipient(store: AgentBusStore, role: str) -> None:
    from identity import Principal

    store.register(
        session_id=role,
        role=role,
        provider="test",
        node="test",
        principal=Principal(id=f"agent-{role}", type="bot", name=role, roles=["bot"]),
        lease_seconds=600,
        mint_token=False,
    )


def test_agent_bus_registers_a_postman_principal(bus_store: AgentBusStore) -> None:
    bus = AgentBus(bus_store)
    row = bus_store.get_principal("postman-bus")
    assert row is not None and row.role == "postman"
    AgentBus(bus_store)  # re-registering (e.g. a second cycle) must not fail
    del bus


def test_agent_bus_delivers_once_on_replay_and_acks(bus_store: AgentBusStore) -> None:
    _register_recipient(bus_store, "claude")
    bus = AgentBus(bus_store)
    delivery = _delivery()

    first_id = bus.send(delivery)
    replay_id = bus.send(delivery)  # same idempotency key: the store treats this as the same message
    assert first_id == replay_id

    inbox = bus_store.list_inbox("claude", 0, 10)
    assert len(inbox["messages"]) == 1

    bus.ack("claude", delivery.board_message_id)
    acked = bus_store.list_inbox("claude", 0, 10)["messages"][0]
    assert acked["acked_at"] is not None


def test_agent_bus_send_rejects_delivery_without_work_number(bus_store: AgentBusStore) -> None:
    bus = AgentBus(bus_store)
    with pytest.raises(ValueError, match="work_number"):
        bus.send(_delivery(work_number=0))


def test_agent_bus_ack_of_unregistered_recipient_fails_closed(bus_store: AgentBusStore) -> None:
    bus = AgentBus(bus_store)
    with pytest.raises(RuntimeError, match="not a registered"):
        bus.ack("nobody", "m1")


# -- RmBoard --------------------------------------------------------------------------------------


def test_rm_board_register_publishes_with_agent_issue_and_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr("coordination.postman_adapters.board.publish", lambda *a: calls.append(a) or {"ok": True})
    RmBoard(repo="Repository_Management").register("claude", "pm-claude", 2376)
    assert calls == [
        (
            "Repository_Management",
            "pm-claude",
            "register",
            "--agent",
            "claude",
            "--issue",
            "2376",
            "--branch",
            "postman-bus",
        )
    ]


def test_rm_board_register_rejects_nonpositive_issue() -> None:
    with pytest.raises(ValueError):
        RmBoard().register("claude", "pm-claude", 0)


def test_rm_board_read_sessions_raises_when_board_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "coordination.postman_adapters.board.read_sessions",
        lambda repo=None: {"available": False, "reason": "boom"},
    )
    with pytest.raises(RuntimeError, match="boom"):
        RmBoard().read_sessions()


def test_rm_board_read_sessions_returns_the_session_list(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [{"session": "pm-claude"}]
    monkeypatch.setattr(
        "coordination.postman_adapters.board.read_sessions",
        lambda repo=None: {"available": True, "sessions": sessions},
    )
    assert RmBoard().read_sessions() == sessions


# -- ConfigBoardSource -----------------------------------------------------------------------------


def _write_config(path: Path, board_issue: object) -> Path:
    cfg_dir = path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "agent_communication.json"
    cfg_path.write_text(json.dumps({"board_issue": board_issue}), encoding="utf-8")
    return path


def test_config_board_source_matches_the_followed_issue(tmp_path: Path) -> None:
    root = _write_config(tmp_path, 2376)
    ref = _ConfigBoardSource(root=root).inspect(2376)
    assert ref == BoardRef(issue=2376)


def test_config_board_source_rolls_a_stale_followed_issue_to_the_configured_one(tmp_path: Path) -> None:
    root = _write_config(tmp_path, 2500)
    ref = _ConfigBoardSource(root=root).inspect(2376)
    assert ref == BoardRef(issue=2376, closed=True, successor=2500)


def test_config_board_source_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        _ConfigBoardSource(root=tmp_path).inspect(2376)


def test_config_board_source_malformed_json_fails_closed(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "agent_communication.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        _ConfigBoardSource(root=tmp_path).inspect(2376)


@pytest.mark.parametrize("bad_value", [0, -1, "2376", True, None])
def test_config_board_source_rejects_bad_board_issue(tmp_path: Path, bad_value: object) -> None:
    root = _write_config(tmp_path, bad_value)
    with pytest.raises(ConfigError):
        _ConfigBoardSource(root=root).inspect(2376)


def test_config_board_source_no_rm_root_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("coordination.postman_adapters.rm_root", lambda: None)
    with pytest.raises(ConfigError):
        _ConfigBoardSource().inspect(2376)


# -- PostmanState.save/load -------------------------------------------------------------------------


def test_postman_state_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = PostmanState(board_issue=2376, delivered=["k1", "k2"])
    state.save(path)
    loaded = PostmanState.load(path, default_board_issue=1)
    assert loaded.board_issue == 2376 and loaded.delivered == ["k1", "k2"]


def test_postman_state_load_missing_file_starts_fresh(tmp_path: Path) -> None:
    loaded = PostmanState.load(tmp_path / "missing.json", default_board_issue=2376)
    assert loaded == PostmanState(board_issue=2376)


# -- postman_run entry point -------------------------------------------------------------------------


def test_dry_run_plans_but_does_not_save_state(tmp_path: Path) -> None:
    board_fake, bus_fake = FakeBoard(), FakeBus()
    board_fake.live("pm-claude", "claude", 100)
    state_path = tmp_path / "state.json"
    pm = Postman(board_fake, bus_fake, Source(BoardRef(issue=2376)), ("claude",), PostmanState(board_issue=2376))
    report = run(pm, state_path, live=False)
    assert report.dry_run is True
    assert not state_path.exists()
    assert board_fake.registered == [] and bus_fake.calls == 0


def test_live_run_applies_the_cycle_and_saves_state(tmp_path: Path) -> None:
    board_fake, bus_fake = FakeBoard(), FakeBus()
    board_fake.live("pm-claude", "claude", 100)
    state_path = tmp_path / "state.json"
    pm = Postman(board_fake, bus_fake, Source(BoardRef(issue=2376)), ("claude",), PostmanState(board_issue=2376))
    report = run(pm, state_path, live=True)
    assert report.dry_run is False
    assert state_path.exists()
    saved = PostmanState.load(state_path, default_board_issue=0)
    assert saved.board_issue == 2376


def test_build_postman_wires_the_real_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bus_store: AgentBusStore
) -> None:
    root = _write_config(tmp_path, 2376)
    monkeypatch.setattr("coordination.postman_run.ConfigBoardSource", lambda: _ConfigBoardSource(root=root))
    monkeypatch.setattr("coordination.postman_run.AgentBusStore", lambda: bus_store)
    monkeypatch.setattr(
        "coordination.postman_run.RmBoard",
        lambda: FakeBoard(),
    )
    pm = build_postman(tmp_path / "state.json", roster=("claude",))
    assert pm.state.board_issue == 2376
