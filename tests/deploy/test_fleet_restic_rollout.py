"""Fleet restic rollout to Desk, ControlTower, and Brick (#4012). Repo-only; no live host changes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fleet_restic_rollout import (
    DEFAULT_HOSTS,
    load_rollout_config,
    plan_restic_rollout,
)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    data = {
        "pilot": {"oglaptop_complete_env": "FLEET_RESTIC_PILOT_COMPLETE"},
        "hosts": [
            {
                "id": "desk",
                "display_name": "Desk",
                "repo_env": "RESTIC_REPOSITORY_DESK",
                "credentials_env": "RESTIC_CREDENTIALS_FILE",
                "backup_paths": ["/var/backups/runner-dashboard"],
            },
            {
                "id": "controltower",
                "display_name": "ControlTower",
                "repo_env": "RESTIC_REPOSITORY_CONTROLTOWER",
                "credentials_env": "RESTIC_CREDENTIALS_FILE",
                "backup_paths": ["/var/backups/runner-dashboard"],
            },
            {
                "id": "brick",
                "display_name": "Brick",
                "repo_env": "RESTIC_REPOSITORY_BRICK",
                "credentials_env": "RESTIC_CREDENTIALS_FILE",
                "backup_paths": ["/var/backups/runner-dashboard"],
            },
        ],
    }
    path = tmp_path / "fleet_restic_hosts.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_default_config_covers_three_fleet_hosts() -> None:
    cfg = load_rollout_config()
    assert [h["id"] for h in cfg["hosts"]] == list(DEFAULT_HOSTS)


def test_rollout_blocked_until_pilot_env_set(config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_RESTIC_PILOT_COMPLETE", raising=False)
    report = plan_restic_rollout(config_path=config_path, apply=False)
    assert report["pilot_ready"] is False
    assert report["would_configure"] == []
    assert any("pilot" in w.lower() for w in report["warnings"])


def test_dry_run_lists_hosts_without_apply_commands(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLEET_RESTIC_PILOT_COMPLETE", "1")
    executed: list[str] = []

    def fake_run(cmd: list[str]) -> int:
        executed.append(" ".join(cmd))
        return 0

    report = plan_restic_rollout(config_path=config_path, apply=False, run=fake_run)
    assert report["pilot_ready"] is True
    assert len(report["would_configure"]) == 3
    assert executed == []


def test_apply_records_install_steps_when_pilot_ready(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLEET_RESTIC_PILOT_COMPLETE", "1")
    executed: list[str] = []

    def fake_run(cmd: list[str]) -> int:
        executed.append(cmd[0])
        return 0

    report = plan_restic_rollout(config_path=config_path, apply=True, run=fake_run)
    assert report["configured"] == 3
    assert executed and all(x == "install" for x in executed)
