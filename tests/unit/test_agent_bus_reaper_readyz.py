"""Host readyz gate for agent_bus stale-session claim reaping (#3823)."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from agent_bus import reaper
from agent_bus.reaper_health import node_reap_gate
from agent_bus.reaper_stats import reset_reaper_stats_for_tests, snapshot

_PEERS = {"Brick": "http://brick:8321"}


def _stale(session_id: str, node: str) -> MagicMock:
    row = MagicMock()
    row.session_id = session_id
    row.role = "issue-remediator"
    row.node = node
    return row


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    store = MagicMock()
    store.release_tracked_claims.return_value = 1
    monkeypatch.setattr(reaper, "get_store", lambda: store)
    reset_reaper_stats_for_tests()
    return store


def _gate(*, local: bool = True, peer: bool = True, calls: list[str] | None = None) -> Callable[[str], bool]:
    seen = calls if calls is not None else []

    def local_ready() -> bool:
        seen.append("local")
        return local

    def peer_ready(url: str) -> bool:
        seen.append(url)
        return peer

    return node_reap_gate(local_machine="Desk", peers=_PEERS, local_ready=local_ready, peer_ready=peer_ready)


@pytest.mark.unit
def test_unhealthy_remote_node_retains_session_and_claims(fake_store: MagicMock) -> None:
    fake_store.list_stale_principals.return_value = [_stale("sess-remote", "brick")]

    result = reaper.reap_once(allows_reap=_gate(peer=False))

    fake_store.release_tracked_claims.assert_not_called()
    fake_store.drop_principal.assert_not_called()
    assert result == {"released_claims": 0, "stale_sessions": 1}
    assert snapshot()["deferred_sessions"] == 1


@pytest.mark.unit
def test_healthy_remote_node_reaps_as_before(fake_store: MagicMock) -> None:
    fake_store.list_stale_principals.return_value = [_stale("sess-remote", "Brick")]

    result = reaper.reap_once(allows_reap=_gate(peer=True))

    fake_store.release_tracked_claims.assert_called_once()
    fake_store.drop_principal.assert_called_once_with("sess-remote")
    assert result["released_claims"] == 1
    assert snapshot()["deferred_sessions"] == 0


@pytest.mark.unit
@pytest.mark.parametrize("node", ["Desk", "desk", "local"])
def test_local_node_uses_local_readyz_not_peer(fake_store: MagicMock, node: str) -> None:
    fake_store.list_stale_principals.return_value = [_stale("sess-local", node)]
    calls: list[str] = []

    reaper.reap_once(allows_reap=_gate(local=False, peer=True, calls=calls))

    assert calls == ["local"]
    fake_store.release_tracked_claims.assert_not_called()
    fake_store.drop_principal.assert_not_called()


@pytest.mark.unit
def test_unknown_node_keeps_existing_reap_behaviour(fake_store: MagicMock) -> None:
    fake_store.list_stale_principals.return_value = [_stale("sess-elsewhere", "laptop-not-in-fleet")]
    calls: list[str] = []

    reaper.reap_once(allows_reap=_gate(local=False, peer=False, calls=calls))

    assert calls == []
    fake_store.drop_principal.assert_called_once_with("sess-elsewhere")


@pytest.mark.unit
def test_readyz_checked_once_per_node_per_pass(fake_store: MagicMock) -> None:
    fake_store.list_stale_principals.return_value = [_stale("a", "Brick"), _stale("b", "brick")]
    calls: list[str] = []

    reaper.reap_once(allows_reap=_gate(peer=False, calls=calls))

    assert calls == ["http://brick:8321"]
    assert snapshot()["deferred_sessions"] == 2


@pytest.mark.unit
def test_gate_rejects_non_string_node() -> None:
    with pytest.raises(TypeError):
        _gate()(None)  # type: ignore[arg-type]
