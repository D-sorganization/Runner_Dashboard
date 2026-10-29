"""Tests for Activity R3 ``fleetctl checkin`` client, bounded offline spool, and tier docs (#2875)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fleetctl
import pytest
from fleet_client import FleetAPIError, FleetArgumentError, FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # pytest fixtures

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_checkin_delivers_canonical_envelope_and_increments_idempotent_seq(
    capsys: pytest.CaptureFixture[str], fake_api: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLEET_CHECKIN_SPOOL", str(tmp_path / "spool.json"))
    monkeypatch.setenv("FLEET_AGENT", "codex")
    monkeypatch.setenv("FLEET_SESSION", "codex-box-20261028-abc123")
    monkeypatch.setenv("FLEET_NODE", "remote-box-1")
    fake_api.respond("POST", "/api/v1/activity/checkins", 200, [{"ok": True}])

    code1 = fleetctl.main(["--url", fake_api.url, "checkin", "started", "--repo", "Runner_Dashboard"])
    out1 = json.loads(capsys.readouterr().out)
    assert code1 == 0 and out1["ok"] is True and not out1["spooled"]
    assert (out1["seq"], out1["delivered"], out1["pending"]) == (1, 1, 0)
    env1 = fake_api.last.body[0]
    assert (env1["schema_version"], env1["source"], env1["principal"]) == (1, "checkin", "agent-codex")
    assert (env1["node"], env1["session_id"], env1["seq"]) == ("remote-box-1", "codex-box-20261028-abc123", 1)
    assert env1["event_id"] == "checkin:codex-box-20261028-abc123:1"
    assert (env1["kind"], env1["repo"], env1["redaction_version"]) == ("started", "Runner_Dashboard", 1)

    code2 = fleetctl.main(
        ["--url", fake_api.url, "checkin", "claimed", "--repo", "Runner_Dashboard", "--issue", "2875"]
    )
    out2 = json.loads(capsys.readouterr().out)
    assert code2 == 0 and out2["seq"] == 2
    assert (fake_api.last.body[0]["seq"], fake_api.last.body[0]["issue"]) == (2, 2875)


def test_checkin_validates_strict_kinds_and_required_fields_before_network(
    capsys: pytest.CaptureFixture[str], fake_api: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLEET_CHECKIN_SPOOL", str(tmp_path / "spool.json"))
    monkeypatch.setenv("FLEET_AGENT", "codex")
    for args, needle in (
        (["checkin", "unknown_kind", "--repo", "Runner_Dashboard"], "unknown checkin kind"),
        (["checkin", "claimed", "--repo", "Runner_Dashboard"], "issue"),
        (["checkin", "pushed", "--repo", "Runner_Dashboard"], "branch"),
        (["checkin", "pr_opened", "--repo", "Runner_Dashboard"], "pr"),
    ):
        code = fleetctl.main(["--url", fake_api.url, *args])
        out = json.loads(capsys.readouterr().out)
        assert code == 2 and out["error"] == "invalid_arguments" and needle in out["message"]
    assert fake_api.requests == []


def test_checkin_retries_with_backoff_and_spools_offline_without_blocking(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = tmp_path / "spool.json"
    sleeps: list[float] = []
    attempts: list[list[dict[str, Any]]] = []

    def failing_request(self: Any, method: str, path: str, **kwargs: Any) -> Any:
        attempts.append(kwargs["body"])
        raise FleetAPIError(0, {"error": "unreachable", "reason": "offline"})

    monkeypatch.setattr(FleetClient, "request", failing_request)
    client = FleetClient("http://127.0.0.1:9", agent="codex", session="codex-offline-20261028-111111")
    res1 = client.checkin(
        "started",
        repo="Runner_Dashboard",
        spool_file=str(spool),
        max_attempts=3,
        base_backoff=0.05,
        sleep_fn=sleeps.append,
    )
    assert res1["ok"] is True and res1["degraded"] is True and res1["spooled"] is True
    assert (res1["delivered"], res1["pending"], res1["seq"], len(attempts)) == (0, 1, 1, 3)
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] > 0

    monkeypatch.setenv("FLEET_CHECKIN_SPOOL", str(spool))
    monkeypatch.setenv("FLEET_AGENT", "codex")
    monkeypatch.setenv("FLEET_SESSION", "codex-offline-20261028-111111")
    code = fleetctl.main(
        ["--url", "http://127.0.0.1:9", "checkin", "pushed", "--repo", "Runner_Dashboard", "--branch", "agy/2875"]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["spooled"] is True and (out["seq"], out["pending"]) == (2, 2)

    sent_batches: list[list[dict[str, Any]]] = []

    def ok_request(self: Any, method: str, path: str, **kw: Any) -> Any:
        sent_batches.append(kw["body"])
        return kw["body"]

    monkeypatch.setattr(FleetClient, "request", ok_request)
    res3 = client.checkin("pr_opened", repo="Runner_Dashboard", pr=3397, spool_file=str(spool))
    assert res3["ok"] is True and not res3["spooled"] and (res3["delivered"], res3["pending"]) == (3, 0)
    assert [e["seq"] for e in sent_batches[0]] == [1, 2, 3]
    assert sent_batches[0][0]["ts"] == attempts[0][0]["ts"]


def test_checkin_spool_is_bounded_and_fails_closed_on_corrupt_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = tmp_path / "spool.json"
    monkeypatch.setattr(
        FleetClient,
        "request",
        lambda self, method, path, **kw: (_ for _ in ()).throw(FleetAPIError(0, {"error": "offline"})),
    )
    client = FleetClient("http://127.0.0.1:9", agent="codex", session="codex-cap-20261028-111111")
    for i in range(1, 6):
        res = client.checkin(
            "heartbeat", repo="Runner_Dashboard", issue=i, spool_file=str(spool), max_spool_events=3, max_attempts=1
        )
    assert (res["pending"], res["dropped"]) == (3, 1)
    saved = json.loads(spool.read_text(encoding="utf-8"))
    assert [item["seq"] for item in saved["queue"]] == [3, 4, 5]

    spool.write_text(json.dumps({"schema_version": 99, "sequences": {}, "queue": []}), encoding="utf-8")
    with pytest.raises(FleetArgumentError, match="spool"):
        client.checkin("started", repo="Runner_Dashboard", spool_file=str(spool), max_attempts=1)


def test_unconnected_tier_docs_and_prompt_snippets_cover_rules() -> None:
    ingestion_doc = (REPO_ROOT / "docs" / "activity-ingestion.md").read_text(encoding="utf-8")
    connect_doc = (REPO_ROOT / "docs" / "agents" / "connect.md").read_text(encoding="utf-8")
    grok_doc = (REPO_ROOT / "docs" / "agents" / "grok.md").read_text(encoding="utf-8")
    workspace_py = (REPO_ROOT / "backend" / "staff" / "workspace.py").read_text(encoding="utf-8")
    briefing_py = (REPO_ROOT / "backend" / "coordination" / "briefing.py").read_text(encoding="utf-8")

    for kind in ("started", "claimed", "pushed", "pr_opened", "blocked", "done"):
        assert kind in ingestion_doc and kind in connect_doc
    assert "fleetctl checkin" in ingestion_doc and "fleetctl checkin" in connect_doc
    assert "fleetctl checkin" in grok_doc and "fleetctl checkin" in workspace_py
    assert "/api/v1/activity/checkins" in briefing_py
