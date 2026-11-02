"""Cross-host health gate for agent_bus claim reaping (#3823)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from agent_bus import reaper
from agent_bus import reaper_health as rh


@pytest.fixture(autouse=True)
def _reset_checks() -> None:
    rh.set_ready_checks(local=None, peer=None)
    yield
    rh.set_ready_checks(local=None, peer=None)


@pytest.mark.unit
def test_reaper_defers_when_remote_node_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    stale_row = MagicMock()
    stale_row.session_id = "sess-remote"
    stale_row.role = "issue-remediator"
    stale_row.node = "node-b"

    fake_store = MagicMock()
    fake_store.list_stale_principals.return_value = [stale_row]

    monkeypatch.setattr(reaper, "get_store", lambda: fake_store)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"node-b": "http://node-b:8321"})
    rh.set_ready_checks(local=lambda: True, peer=lambda _n, _u: False)

    result = reaper.reap_once()
    fake_store.release_tracked_claims.assert_not_called()
    fake_store.drop_principal.assert_not_called()
    assert result["released_claims"] == 0
    assert result["stale_sessions"] == 1


@pytest.mark.unit
def test_reaper_releases_when_remote_node_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    stale_row = MagicMock()
    stale_row.session_id = "sess-remote"
    stale_row.role = "issue-remediator"
    stale_row.node = "node-b"

    fake_store = MagicMock()
    fake_store.list_stale_principals.return_value = [stale_row]
    fake_store.release_tracked_claims.return_value = 1

    monkeypatch.setattr(reaper, "get_store", lambda: fake_store)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"node-b": "http://node-b:8321"})
    rh.set_ready_checks(local=lambda: True, peer=lambda _n, _u: True)
    monkeypatch.setattr(
        "coordination.claims.release",
        lambda *a, **k: {"released": True, "result": {}, "warnings": []},
    )

    result = reaper.reap_once()
    fake_store.release_tracked_claims.assert_called_once()
    fake_store.drop_principal.assert_called_once_with("sess-remote")
    assert result["released_claims"] == 1
