"""inbox_webhook_url merge from agent_principals.yml (#3682 slice 4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from agent_principal_defaults import merge_agent_principal_defaults
from identity import IdentityManager


@pytest.mark.unit
def test_merge_applies_inbox_webhook_and_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "agent_principals.yml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "agents": [
                    {
                        "id": "agent-grok",
                        "inbox_webhook_url": "https://example.invalid/hook",
                        "inbox_roles": ["barb", "rd-barb"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(cfg))
    id_dir = tmp_path / "id"
    id_dir.mkdir()
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir()
    (tok_dir / "agent-grok.token").write_text("svc_test_grok_webhook\n", encoding="utf-8")

    mgr = IdentityManager(config_dir=id_dir)
    merge_agent_principal_defaults(mgr)

    prin = mgr.principals["agent-grok"]
    assert prin.inbox_webhook_url == "https://example.invalid/hook"
    assert prin.inbox_roles == ["barb", "rd-barb"]
