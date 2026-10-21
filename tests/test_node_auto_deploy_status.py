"""Fleet status embeds auto-deploy telemetry."""

from __future__ import annotations

import json
from pathlib import Path

from node_auto_deploy_status import read_auto_deploy_fleet_status


def test_read_auto_deploy_fleet_status_from_file(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "auto_deploy_state.json"
    state.write_text(
        json.dumps(
            {
                "attention": True,
                "last_result": "rollback",
                "events": [{"result": "rollback"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RUNNER_DASHBOARD_AUTO_DEPLOY_STATE", str(state))
    payload = read_auto_deploy_fleet_status()
    assert payload["attention"] is True
    assert payload["last_result"] == "rollback"
    assert payload["recent_events"][0]["result"] == "rollback"
