"""Agent token health report and Prometheus lines (#3895 slice 1).

Every token here is a fixture minted into a temporary identity store; the report
must never echo a token value back.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from agent_token_health import render_token_health_lines, token_health_report
from identity import IdentityManager

_DAY = 86400.0


def _defaults(tmp_path: Path) -> Path:
    path = tmp_path / "agent_principals.yml"
    path.write_text(
        "agents:\n  - id: agent-grok\n    kind: grokbot\n  - id: agent-codex\n    kind: codex\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def id_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(_defaults(tmp_path)))
    monkeypatch.delenv("RD_OVERLAY_PATH", raising=False)
    path = tmp_path / "id"
    (path / "agent-tokens").mkdir(parents=True)
    return path


def _write_token(id_dir: Path, agent_id: str, raw: str) -> None:
    (id_dir / "agent-tokens" / f"{agent_id}.token").write_text(raw + "\n", encoding="utf-8")


def _minted(id_dir: Path, agent_id: str, days: int | None) -> tuple[IdentityManager, str]:
    """Mint a fixture token for a provisioned principal and write its token file."""
    _write_token(id_dir, agent_id, "svc_fixture_bootstrap")
    mgr = IdentityManager(config_dir=id_dir)
    raw = mgr.mint_service_token(agent_id, name=f"agent-tokens/{agent_id}.token", expires_in_days=days)
    _write_token(id_dir, agent_id, raw)
    return mgr, raw


def _row(report: list[dict], agent_id: str) -> dict:
    return next(row for row in report if row["principal"] == agent_id)


def test_every_configured_principal_is_reported(id_dir: Path) -> None:
    report = token_health_report(IdentityManager(config_dir=id_dir))
    assert [row["principal"] for row in report] == ["agent-codex", "agent-grok"]


def test_missing_token_file(id_dir: Path) -> None:
    row = _row(token_health_report(IdentityManager(config_dir=id_dir)), "agent-grok")
    assert row["present"] is False
    assert row["valid"] is False
    assert row["reason"] == "token_file_missing"
    assert row["days_to_expiry"] is None


def test_valid_token_reports_days_to_expiry(id_dir: Path) -> None:
    mgr, _raw = _minted(id_dir, "agent-grok", days=30)
    row = _row(token_health_report(mgr), "agent-grok")
    assert row["present"] is True
    assert row["valid"] is True
    assert row["reason"] is None
    assert 29.9 < row["days_to_expiry"] <= 30.0
    assert row["expires_at"] is not None


def test_token_without_expiry_is_valid_with_no_deadline(id_dir: Path) -> None:
    _write_token(id_dir, "agent-grok", "svc_fixture_no_expiry")
    row = _row(token_health_report(IdentityManager(config_dir=id_dir)), "agent-grok")
    assert row["valid"] is True
    assert row["expires_at"] is None
    assert row["days_to_expiry"] is None


def test_expired_token_is_rejected_with_negative_remaining(id_dir: Path) -> None:
    mgr, _raw = _minted(id_dir, "agent-grok", days=30)
    record = next(t for t in mgr.tokens if t.principal_id == "agent-grok" and t.expires_at)
    record.expires_at = time.time() - 2 * _DAY
    row = _row(token_health_report(mgr), "agent-grok")
    assert row["valid"] is False
    assert row["reason"] == "token_expired"
    assert row["days_to_expiry"] < 0


def test_token_file_not_known_to_identity_store_is_rejected(id_dir: Path) -> None:
    mgr = IdentityManager(config_dir=id_dir)
    _write_token(id_dir, "agent-grok", "svc_fixture_replaced_after_startup")
    row = _row(token_health_report(mgr), "agent-grok")
    assert row["present"] is True
    assert row["valid"] is False
    assert row["reason"] == "token_hash_not_registered"


def test_token_for_another_principal_is_rejected(id_dir: Path) -> None:
    mgr, raw = _minted(id_dir, "agent-grok", days=30)
    _write_token(id_dir, "agent-codex", raw)
    row = _row(token_health_report(mgr), "agent-codex")
    assert row["valid"] is False
    assert row["reason"] == "principal_mismatch"


def test_empty_token_file_is_reported_as_empty(id_dir: Path) -> None:
    _write_token(id_dir, "agent-grok", "")
    row = _row(token_health_report(IdentityManager(config_dir=id_dir)), "agent-grok")
    assert row["present"] is True
    assert row["valid"] is False
    assert row["reason"] == "token_file_empty"


def test_report_never_contains_the_token_or_its_hash(id_dir: Path) -> None:
    mgr, raw = _minted(id_dir, "agent-grok", days=30)
    dumped = json.dumps(token_health_report(mgr))
    assert raw not in dumped
    assert all(t.token_hash not in dumped for t in mgr.tokens)


def test_prometheus_lines_carry_expiry_seconds_per_principal(id_dir: Path) -> None:
    mgr, _raw = _minted(id_dir, "agent-grok", days=10)
    text = "\n".join(render_token_health_lines(token_health_report(mgr)))
    assert "# TYPE rd_agent_token_expiry_seconds gauge" in text
    prefix = 'rd_agent_token_expiry_seconds{principal="agent-grok"}'
    grok = next(line for line in text.splitlines() if line.startswith(prefix))
    assert 9.9 * _DAY < float(grok.rsplit(" ", 1)[1]) <= 10 * _DAY
    assert 'rd_agent_token_expiry_seconds{principal="agent-codex"}' not in text
    assert 'rd_agent_token_present{principal="agent-codex"} 0' in text
    assert 'rd_agent_token_valid{principal="agent-grok"} 1' in text


def test_metrics_exporter_renders_agent_token_lines() -> None:
    from routers.metrics import render_prometheus_text

    row = {"principal": "agent-grok", "present": True, "valid": True, "expires_at": 1.0, "seconds_to_expiry": 60.0}
    text = render_prometheus_text({"agent_tokens": [row]})
    assert 'rd_agent_token_expiry_seconds{principal="agent-grok"} 60' in text


def test_admin_route_requires_admin_scope_and_returns_report(id_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import admin

    route = next(r for r in admin.router.routes if getattr(r, "path", "") == "/api/admin/token-health")
    assert route.methods == {"GET"}
    mgr, raw = _minted(id_dir, "agent-grok", days=30)
    monkeypatch.setattr(admin, "identity_manager", mgr)
    body = admin.get_token_health()
    assert _row(body["principals"], "agent-grok")["valid"] is True
    assert raw not in json.dumps(body)
