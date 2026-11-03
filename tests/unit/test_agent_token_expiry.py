"""Agent token expiry warnings at 14 and 3 days (#3895 slice 2).

Every token is a fixture minted into a temporary identity store; no alert,
log line or state file may carry a token value or hash.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import agent_token_expiry as ate
import pytest
from agent_token_expiry import check_token_expiry, due_warnings
from agent_token_health import token_health_report
from identity import IdentityManager

_DAY = 86400.0


def _row(principal: str, days: float | None, *, valid: bool = True, expires_at: float = 5_000_000.0) -> dict:
    return {
        "principal": principal,
        "present": True,
        "valid": valid,
        "reason": None if valid else "token_expired",
        "expires_at": None if days is None else expires_at,
        "seconds_to_expiry": None if days is None else days * _DAY,
        "days_to_expiry": days,
    }


def test_no_warning_beyond_fourteen_days() -> None:
    assert due_warnings([_row("agent-grok", 20.0)], sent=set()) == []


def test_fourteen_day_warning() -> None:
    [alert] = due_warnings([_row("agent-grok", 13.5)], sent=set())
    assert alert["principal"] == "agent-grok"
    assert alert["threshold_days"] == 14
    assert "agent-grok" in alert["title"]


def test_three_day_warning_supersedes_fourteen() -> None:
    [alert] = due_warnings([_row("agent-grok", 2.0)], sent=set())
    assert alert["threshold_days"] == 3


def test_each_threshold_alerts_once_per_token() -> None:
    [first] = due_warnings([_row("agent-grok", 13.0)], sent=set())
    assert due_warnings([_row("agent-grok", 12.0)], sent={first["key"]}) == []
    [second] = due_warnings([_row("agent-grok", 2.5)], sent={first["key"]})
    assert second["threshold_days"] == 3


def test_a_new_token_rearms_the_warnings() -> None:
    [first] = due_warnings([_row("agent-grok", 13.0)], sent=set())
    rotated = _row("agent-grok", 13.0, expires_at=9_000_000.0)
    assert len(due_warnings([rotated], sent={first["key"]})) == 1


@pytest.mark.parametrize(
    "row",
    [_row("agent-grok", None), _row("agent-grok", -1.0, valid=False), _row("agent-grok", 2.0, valid=False)],
    ids=["no-expiry", "expired", "rejected"],
)
def test_no_expiry_warning_without_a_live_deadline(row: dict) -> None:
    """Expired or rejected tokens belong to the liveness alert (slice 4), not this one."""
    assert due_warnings([row], sent=set()) == []


@pytest.fixture
def id_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    defaults = tmp_path / "agent_principals.yml"
    defaults.write_text("agents:\n  - id: agent-grok\n    kind: grokbot\n", encoding="utf-8")
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
    monkeypatch.delenv("RD_OVERLAY_PATH", raising=False)
    path = tmp_path / "id"
    (path / "agent-tokens").mkdir(parents=True)
    return path


def _minted(id_dir: Path, days: int) -> tuple[IdentityManager, str]:
    token_file = id_dir / "agent-tokens" / "agent-grok.token"
    token_file.write_text("svc_fixture_bootstrap\n", encoding="utf-8")
    mgr = IdentityManager(config_dir=id_dir)
    raw = mgr.mint_service_token("agent-grok", name="agent-tokens/agent-grok.token", expires_in_days=days)
    token_file.write_text(raw + "\n", encoding="utf-8")
    return mgr, raw


def test_check_emits_once_and_persists_across_restarts(id_dir: Path, tmp_path: Path) -> None:
    mgr, _raw = _minted(id_dir, days=10)
    state = tmp_path / "warnings.json"
    sent: list[dict] = []
    assert len(check_token_expiry(mgr, state_path=state, emit=sent.append)) == 1
    assert check_token_expiry(mgr, state_path=state, emit=sent.append) == []
    assert [a["threshold_days"] for a in sent] == [14]


def test_corrupt_state_file_still_warns(id_dir: Path, tmp_path: Path) -> None:
    mgr, _raw = _minted(id_dir, days=2)
    state = tmp_path / "warnings.json"
    state.write_text("{not json", encoding="utf-8")
    sent: list[dict] = []
    check_token_expiry(mgr, state_path=state, emit=sent.append)
    assert [a["threshold_days"] for a in sent] == [3]


def test_alerts_and_state_never_carry_the_token(id_dir: Path, tmp_path: Path) -> None:
    mgr, raw = _minted(id_dir, days=2)
    state = tmp_path / "warnings.json"
    sent: list[dict] = []
    check_token_expiry(mgr, state_path=state, emit=sent.append)
    blob = json.dumps(sent) + state.read_text(encoding="utf-8")
    assert raw not in blob
    assert all(t.token_hash not in blob for t in mgr.tokens)


def test_emit_writes_fleet_event_push_and_sentinel_inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import push
    from fleet_events import EventStore

    store = EventStore()
    monkeypatch.setattr("fleet_events.get_event_store", lambda: store)
    pushes: list[tuple] = []
    monkeypatch.setattr(push, "notify", lambda *args: pushes.append(args))
    inbox = tmp_path / "barb-inbox.log"
    monkeypatch.setenv("HUB_SENTINEL_BARB_INBOX", str(inbox))

    [alert] = due_warnings([_row("agent-grok", 2.0)], sent=set())
    ate.emit_expiry_warning(alert)

    [event] = store.recent()
    assert event.severity == "warning"
    assert "agent-grok" in event.title
    assert pushes and pushes[0][0] == "staff.escalation"
    assert "agent-grok" in inbox.read_text(encoding="utf-8")


def test_unreadable_token_file_is_reported_not_raised(id_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lane 3 follow-up: an unreadable token file is a health row, never a 500."""
    (id_dir / "agent-tokens" / "agent-grok.token").write_bytes(b"\xff\xfe\xfa")
    mgr = IdentityManager.__new__(IdentityManager)
    mgr.config_dir, mgr.tokens, mgr.principals = id_dir, [], {}
    mgr._ephemeral_tokens, mgr._ephemeral_principals = {}, {}
    [row] = token_health_report(mgr)
    assert row["present"] is True
    assert row["valid"] is False
    assert row["reason"] == "token_file_unreadable"


def test_metrics_token_rows_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Lane 3 follow-up: a failing token health check is logged, not swallowed silently."""
    import agent_token_health
    from routers import metrics

    def _boom(_manager: object) -> list:
        raise OSError("identity store unavailable")

    monkeypatch.setattr(agent_token_health, "token_health_report", _boom)
    with caplog.at_level(logging.WARNING):
        assert metrics._agent_token_rows() == []
    assert "agent token health" in caplog.text


def test_token_health_route_rejects_non_admin(make_authed_client, viewer_principal) -> None:
    """Lane 3 follow-up: the admin route answers 403 to a principal without the admin scope."""
    client = make_authed_client(viewer_principal)
    assert client.get("/api/admin/token-health").status_code == 403
