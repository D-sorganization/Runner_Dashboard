"""Agent principals live config drift reporting (#4257)."""

from __future__ import annotations

from pathlib import Path

import yaml
from agent_principals_drift import agent_principals_config_drift


def _write_principals(path: Path, *, barb_role: bool) -> None:
    roles = ["bot", "barb"] if barb_role else ["bot"]
    payload = {
        "assistant_profile": {"roles": ["bot"]},
        "agents": [{"id": "agent-grok", "roles": roles}],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_drift_detects_stale_live_file_missing_barb_role(tmp_path: Path) -> None:
    repo = tmp_path / "repo" / "config"
    repo.mkdir(parents=True)
    live = tmp_path / "live" / "agent_principals.yml"
    live.parent.mkdir(parents=True)

    _write_principals(repo / "agent_principals.yml", barb_role=True)
    _write_principals(live, barb_role=False)

    drift = agent_principals_config_drift(repo_defaults=repo / "agent_principals.yml", live_path=live)

    assert drift["checked"] is True
    assert drift["drifted"] is True
    assert drift["content_sha256_match"] is False
    assert "agent-grok" in drift["barb_role_missing_on_live"]


def test_no_drift_when_live_matches_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo" / "config"
    repo.mkdir(parents=True)
    live = tmp_path / "live" / "agent_principals.yml"
    live.parent.mkdir(parents=True)

    _write_principals(repo / "agent_principals.yml", barb_role=True)
    live.write_bytes((repo / "agent_principals.yml").read_bytes())

    drift = agent_principals_config_drift(repo_defaults=repo / "agent_principals.yml", live_path=live)

    assert drift["checked"] is True
    assert drift["drifted"] is False
    assert drift["content_sha256_match"] is True
    assert drift["barb_role_missing_on_live"] == []