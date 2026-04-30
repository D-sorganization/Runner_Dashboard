"""Tests for session_management.py utilities."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from session_management import (
    generate_session_id,
    hash_session_id,
    is_session_active,
    list_sessions_for_principal,
    register_session,
    revoke_all_sessions_for_principal,
    revoke_session,
    session_count_for_principal,
)


def _set_sessions_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr("session_management._SESSIONS_PATH", sessions_path)
    # Clear any stale data by writing empty list
    sessions_path.write_text("[]")
    return sessions_path


def test_generate_session_id_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sessions_path(tmp_path, monkeypatch)
    sid = generate_session_id()
    assert sid.startswith("sess_")
    assert len(sid) > 30


def test_register_and_list_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sessions_path(tmp_path, monkeypatch)

    sid1 = register_session("user-1", user_agent="Mozilla/5.0", ip_address="127.0.0.1")
    sid2 = register_session("user-1", user_agent="curl/7.0", ip_address="10.0.0.1")
    sid3 = register_session("user-2", user_agent="Mozilla/5.0", ip_address="127.0.0.1")

    assert is_session_active(sid1)
    assert is_session_active(sid2)
    assert is_session_active(sid3)

    sessions = list_sessions_for_principal("user-1")
    assert len(sessions) == 2
    assert {s["user_agent"] for s in sessions} == {"Mozilla/5.0", "curl/7.0"}
    assert session_count_for_principal("user-1") == 2
    assert session_count_for_principal("user-2") == 1


def test_revoke_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sessions_path(tmp_path, monkeypatch)

    sid = register_session("user-1")
    assert is_session_active(sid)

    result = revoke_session(sid)
    assert result is True
    assert not is_session_active(sid)

    # Idempotent: revoking again returns False
    result2 = revoke_session(sid)
    assert result2 is False


def test_revoke_all_sessions_for_principal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sessions_path(tmp_path, monkeypatch)

    sid1 = register_session("user-1")
    sid2 = register_session("user-1")
    sid3 = register_session("user-1")
    sid_other = register_session("user-2")

    count = revoke_all_sessions_for_principal("user-1")
    assert count == 3
    assert not is_session_active(sid1)
    assert not is_session_active(sid2)
    assert not is_session_active(sid3)
    assert is_session_active(sid_other)


def test_revoke_all_except_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sessions_path(tmp_path, monkeypatch)

    sid_keep = register_session("user-1")
    sid_revoke = register_session("user-1")

    count = revoke_all_sessions_for_principal("user-1", exclude_session_id=sid_keep)
    assert count == 1
    assert is_session_active(sid_keep)
    assert not is_session_active(sid_revoke)


def test_prune_expired_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sessions_path(tmp_path, monkeypatch)
    monkeypatch.setattr("session_management._SESSION_TTL_SECONDS", 1)

    sid = register_session("user-1")
    assert is_session_active(sid)

    time.sleep(1.5)
    assert not is_session_active(sid)
    assert session_count_for_principal("user-1") == 0


def test_hash_session_id_is_stable() -> None:
    sid = "sess_test_123"
    h1 = hash_session_id(sid)
    h2 = hash_session_id(sid)
    assert h1 == h2
    assert h1 != sid
    assert len(h1) > 10


def test_max_sessions_fifo_eviction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sessions_path(tmp_path, monkeypatch)
    monkeypatch.setattr("session_management._MAX_SESSIONS_PER_PRINCIPAL", 3)

    sid1 = register_session("user-1")
    time.sleep(0.01)
    sid2 = register_session("user-1")
    time.sleep(0.01)
    sid3 = register_session("user-1")
    time.sleep(0.01)
    sid4 = register_session("user-1")

    # Oldest (sid1) should have been evicted
    assert not is_session_active(sid1)
    assert is_session_active(sid2)
    assert is_session_active(sid3)
    assert is_session_active(sid4)
    assert session_count_for_principal("user-1") == 3
