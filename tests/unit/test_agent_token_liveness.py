"""Agent token liveness alert: missing or rejected token file (#3895 slice 4).

Every token is a fixture minted into a temporary identity store; no alert or
state entry may carry a token value or hash.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_token_liveness import LIVENESS_INTERVAL_SECONDS, check_token_liveness, liveness_alerts
from identity import IdentityManager, Principal


def _row(principal: str, *, present: bool, valid: bool, reason: str | None) -> dict:
    return {"principal": principal, "present": present, "valid": valid, "reason": reason}


_MISSING = _row("agent-grok", present=False, valid=False, reason="token_file_missing")
_REJECTED = _row("agent-grok", present=True, valid=False, reason="token_hash_not_registered")
_HEALTHY = _row("agent-grok", present=True, valid=True, reason=None)


def test_check_runs_within_five_minutes() -> None:
    assert LIVENESS_INTERVAL_SECONDS <= 300


def test_missing_file_of_a_known_principal_alerts() -> None:
    [alert], open_alerts = liveness_alerts([_MISSING], known={"agent-grok"}, open_alerts={})
    assert alert["principal"] == "agent-grok"
    assert alert["severity"] == "critical"
    assert "missing" in alert["title"]
    assert open_alerts == {"agent-grok": "token_file_missing"}


def test_missing_file_of_a_principal_never_provisioned_here_is_silent() -> None:
    """A configured principal whose token lives on another host is not an outage here."""
    assert liveness_alerts([_MISSING], known=set(), open_alerts={}) == ([], {})


def test_rejected_token_alerts_even_if_not_yet_known() -> None:
    [alert], _open = liveness_alerts([_REJECTED], known=set(), open_alerts={})
    assert "rejected" in alert["title"]
    assert "token_hash_not_registered" in alert["detail"]


def test_an_open_outage_alerts_once() -> None:
    _alerts, open_alerts = liveness_alerts([_MISSING], known={"agent-grok"}, open_alerts={})
    assert liveness_alerts([_MISSING], known={"agent-grok"}, open_alerts=open_alerts) == ([], open_alerts)


def test_a_changed_failure_alerts_again() -> None:
    _alerts, open_alerts = liveness_alerts([_MISSING], known={"agent-grok"}, open_alerts={})
    [alert], open_alerts = liveness_alerts([_REJECTED], known={"agent-grok"}, open_alerts=open_alerts)
    assert open_alerts == {"agent-grok": "token_hash_not_registered"}


def test_recovery_closes_the_outage_so_the_next_one_alerts() -> None:
    _alerts, open_alerts = liveness_alerts([_MISSING], known={"agent-grok"}, open_alerts={})
    assert liveness_alerts([_HEALTHY], known={"agent-grok"}, open_alerts=open_alerts) == ([], {})
    [_again], _open = liveness_alerts([_MISSING], known={"agent-grok"}, open_alerts={})


@pytest.fixture
def id_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    defaults = tmp_path / "agent_principals.yml"
    defaults.write_text("agents:\n  - id: agent-grok\n    kind: grokbot\n  - id: agent-codex\n", encoding="utf-8")
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
    monkeypatch.delenv("RD_OVERLAY_PATH", raising=False)
    path = tmp_path / "id"
    (path / "agent-tokens").mkdir(parents=True)
    return path


def _token_file(id_dir: Path) -> Path:
    return id_dir / "agent-tokens" / "agent-grok.token"


def test_deleting_a_fixture_token_file_raises_the_alert(id_dir: Path, tmp_path: Path) -> None:
    """Acceptance: deleting agent-grok.token raises the liveness alert on the next check."""
    _token_file(id_dir).write_text("svc_fixture_liveness\n", encoding="utf-8")
    mgr = IdentityManager(config_dir=id_dir)
    state = tmp_path / "liveness.json"
    sent: list[dict] = []

    assert check_token_liveness(mgr, state_path=state, emit=sent.append) == []
    _token_file(id_dir).unlink()
    check_token_liveness(mgr, state_path=state, emit=sent.append)
    check_token_liveness(mgr, state_path=state, emit=sent.append)

    assert [(a["principal"], a["severity"]) for a in sent] == [("agent-grok", "critical")]


def test_deletion_is_still_caught_after_a_restart(id_dir: Path, tmp_path: Path) -> None:
    """The known set persists, so a restart after the file vanished still alerts."""
    _token_file(id_dir).write_text("svc_fixture_restart\n", encoding="utf-8")
    state = tmp_path / "liveness.json"
    check_token_liveness(IdentityManager(config_dir=id_dir), state_path=state, emit=lambda _a: None)
    _token_file(id_dir).unlink()

    sent: list[dict] = []
    check_token_liveness(IdentityManager(config_dir=id_dir), state_path=state, emit=sent.append)
    assert [a["principal"] for a in sent] == ["agent-grok"]


def test_minted_file_token_counts_as_provisioned(id_dir: Path, tmp_path: Path) -> None:
    """A token minted for agent-tokens/<id>.token marks the principal as expected on this host."""
    mgr = IdentityManager(config_dir=id_dir)
    mgr.principals["agent-codex"] = Principal(id="agent-codex", type="bot", name="agent-codex", roles=["bot"])
    mgr.mint_service_token("agent-codex", name="agent-tokens/agent-codex.token", expires_in_days=30)
    sent: list[dict] = []
    check_token_liveness(mgr, state_path=tmp_path / "liveness.json", emit=sent.append)
    assert [a["principal"] for a in sent] == ["agent-codex"]


def test_corrupt_state_file_still_alerts_on_a_rejected_token(id_dir: Path, tmp_path: Path) -> None:
    mgr = IdentityManager(config_dir=id_dir)
    _token_file(id_dir).write_text("svc_fixture_unregistered\n", encoding="utf-8")
    state = tmp_path / "liveness.json"
    state.write_text("[not a dict", encoding="utf-8")
    sent: list[dict] = []
    check_token_liveness(mgr, state_path=state, emit=sent.append)
    assert [a["principal"] for a in sent] == ["agent-grok"]


def test_alerts_and_state_never_carry_the_token(id_dir: Path, tmp_path: Path) -> None:
    mgr = IdentityManager(config_dir=id_dir)
    raw = "svc_fixture_secret_value"
    _token_file(id_dir).write_text(raw + "\n", encoding="utf-8")
    state = tmp_path / "liveness.json"
    sent: list[dict] = []
    check_token_liveness(mgr, state_path=state, emit=sent.append)
    blob = json.dumps(sent) + state.read_text(encoding="utf-8")
    assert sent
    assert raw not in blob
    assert all(t.token_hash not in blob for t in mgr.tokens)
