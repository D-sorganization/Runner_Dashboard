"""Contract tests enforcing the #2116 deploy smoke job TDD requirement (#2255)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_contributing_documents_deploy_smoke_tdd_rule() -> None:
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "2116" in text, "CONTRIBUTING.md must reference the #2116 deploy smoke test requirement"
    lower = text.lower()
    assert "deploy smoke" in lower or "auto-deploy" in lower
    assert "failing regression" in lower or "failing test" in lower or "tdd" in lower


def test_agents_documents_deploy_smoke_tdd_rule() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "2116" in text, "AGENTS.md must reference the #2116 deploy smoke test requirement"
    lower = text.lower()
    assert "deploy smoke" in lower or "auto-deploy" in lower
    assert "failing" in lower or "regression" in lower


def test_node_auto_deploy_runbook_documents_tdd_requirement() -> None:
    text = (ROOT / "docs" / "runbooks" / "node-auto-deploy.md").read_text(encoding="utf-8")
    assert "2116" in text, "docs/runbooks/node-auto-deploy.md must reference #2116 regression tests"
    assert "regression" in text.lower()
