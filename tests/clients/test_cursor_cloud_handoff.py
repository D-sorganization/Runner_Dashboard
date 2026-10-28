"""Tests for Cursor cloud handoff check-in emission (#3377)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "clients" / "fleet", REPO_ROOT / "tests" / "clients"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fleet_client import FleetAPIError, FleetArgumentError, FleetClient  # noqa: E402

from scripts import cursor_cloud_handoff  # noqa: E402

pytest_plugins = ("fleet_fixtures",)


def test_emit_handoff_checkin_pr_opened_and_done_when_hub_reachable(
    fake_api: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = tmp_path / "spool.json"
    monkeypatch.setenv("FLEET_CHECKIN_TOKEN", "test-checkin-token")
    monkeypatch.setenv("FLEET_NODE", "cloud-box-1")
    fake_api.respond("POST", "/api/v1/activity/checkins", 200, [{"ok": True}])

    res_pr = cursor_cloud_handoff.emit_handoff_checkin(
        "pr_opened",
        "Runner_Dashboard",
        pr=3377,
        issue=3377,
        branch="cursor/activity-r1-followup-3377",
        base_url=fake_api.url,
        session="cursor-cloud-20261028-abc123",
        spool_file=spool,
    )
    assert res_pr["ok"] is True
    assert res_pr["delivered"] == 1
    assert not res_pr.get("degraded")
    env_pr = fake_api.last.body[0]
    assert (env_pr["source"], env_pr["kind"], env_pr["principal"]) == ("checkin", "pr_opened", "agent-cursor")
    assert (env_pr["repo"], env_pr["pr"], env_pr["issue"]) == ("Runner_Dashboard", 3377, 3377)
    assert env_pr["branch"] == "cursor/activity-r1-followup-3377"
    assert fake_api.last.headers.get("authorization") == "Bearer test-checkin-token"

    res_done = cursor_cloud_handoff.emit_handoff_checkin(
        "done",
        "D-sorganization/Runner_Dashboard",
        pr=3377,
        issue=3377,
        branch="cursor/activity-r1-followup-3377",
        base_url=fake_api.url,
        session="cursor-cloud-20261028-abc123",
        spool_file=spool,
    )
    assert res_done["ok"] is True
    assert res_done["delivered"] == 1
    env_done = fake_api.last.body[0]
    assert (env_done["source"], env_done["kind"], env_done["repo"]) == ("checkin", "done", "Runner_Dashboard")
    assert env_done["seq"] == 2


def test_emit_handoff_checkin_validates_preconditions_before_network(
    fake_api: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLEET_CHECKIN_TOKEN", "test-checkin-token")
    spool = tmp_path / "spool.json"

    with pytest.raises(FleetArgumentError, match="kind"):
        cursor_cloud_handoff.emit_handoff_checkin(
            "started", "Runner_Dashboard", base_url=fake_api.url, spool_file=spool
        )

    with pytest.raises(FleetArgumentError, match="pr"):
        cursor_cloud_handoff.emit_handoff_checkin(
            "pr_opened", "Runner_Dashboard", base_url=fake_api.url, spool_file=spool
        )

    with pytest.raises(FleetArgumentError, match="repo"):
        cursor_cloud_handoff.emit_handoff_checkin("done", "", base_url=fake_api.url, spool_file=spool)

    assert fake_api.requests == []


def test_emit_handoff_checkin_fails_open_when_hub_unreachable_or_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = tmp_path / "spool.json"
    monkeypatch.delenv("FLEET_CHECKIN_TOKEN", raising=False)
    monkeypatch.delenv("FLEET_API_TOKEN", raising=False)

    no_tok = cursor_cloud_handoff.emit_handoff_checkin(
        "done", "Runner_Dashboard", base_url="http://127.0.0.1:9", spool_file=spool
    )
    assert no_tok["ok"] is True and no_tok["skipped"] is True
    assert no_tok["reason"] == "no_checkin_token"

    monkeypatch.setenv("FLEET_CHECKIN_TOKEN", "test-checkin-token")
    monkeypatch.setattr(
        FleetClient,
        "request",
        lambda self, method, path, **kw: (_ for _ in ()).throw(FleetAPIError(0, {"error": "unreachable"})),
    )
    offline = cursor_cloud_handoff.emit_handoff_checkin(
        "pr_opened", "Runner_Dashboard", pr=3377, base_url="http://127.0.0.1:9", spool_file=spool
    )
    assert offline["ok"] is True and offline["degraded"] is True

    monkeypatch.setattr(
        FleetClient,
        "request",
        lambda self, method, path, **kw: (_ for _ in ()).throw(FleetAPIError(403, {"detail": "forbidden"})),
    )
    denied = cursor_cloud_handoff.emit_handoff_checkin(
        "done", "Runner_Dashboard", base_url="http://127.0.0.1:9", spool_file=spool
    )
    assert denied["ok"] is True and denied["degraded"] is True
    assert denied["reason"] == "http_403"


def test_handoff_stop_cli_emits_pr_opened_and_done_and_exits_zero_offline(
    fake_api: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spool = tmp_path / "spool.json"
    monkeypatch.setenv("FLEET_CHECKIN_SPOOL", str(spool))
    monkeypatch.setenv("FLEET_CHECKIN_TOKEN", "test-checkin-token")
    monkeypatch.setenv("FLEET_API_URL", fake_api.url)
    monkeypatch.setenv("CURSOR_CLOUD_HANDOFF_TEST_REPO", "Runner_Dashboard")
    monkeypatch.setenv("CURSOR_CLOUD_HANDOFF_TEST_BRANCH", "cursor/test-3377")
    monkeypatch.setenv("CURSOR_CLOUD_HANDOFF_TEST_ISSUE", "3377")
    monkeypatch.setenv("CURSOR_CLOUD_HANDOFF_TEST_PR_NUMBER", "3418")
    fake_api.respond("POST", "/api/v1/activity/checkins", 200, [{"ok": True}])

    code = cursor_cloud_handoff.main(["stop"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out == {}
    kinds = [req.body[0]["kind"] for req in fake_api.requests]
    assert kinds == ["pr_opened", "done"]
    assert fake_api.requests[0].body[0]["pr"] == 3418
    assert fake_api.requests[1].body[0]["repo"] == "Runner_Dashboard"

    monkeypatch.setenv("FLEET_API_URL", "http://127.0.0.1:9")
    code_offline = cursor_cloud_handoff.main(["stop"])
    out_offline = json.loads(capsys.readouterr().out)
    assert code_offline == 0 and out_offline == {}


def test_handoff_checkin_cli_subcommand_and_done_only_stop(
    fake_api: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spool = tmp_path / "spool.json"
    monkeypatch.setenv("FLEET_CHECKIN_SPOOL", str(spool))
    monkeypatch.setenv("FLEET_CHECKIN_TOKEN", "test-checkin-token")
    monkeypatch.setenv("FLEET_API_URL", fake_api.url)
    fake_api.respond("POST", "/api/v1/activity/checkins", 200, [{"ok": True}])

    code_bad = cursor_cloud_handoff.main(["checkin", "pr_opened", "--repo", "Runner_Dashboard", "--url", fake_api.url])
    out_bad = json.loads(capsys.readouterr().out)
    assert code_bad == 2 and out_bad["error"] == "invalid_arguments"

    code_ok = cursor_cloud_handoff.main(
        ["checkin", "pr_opened", "--repo", "Runner_Dashboard", "--pr", "3377", "--url", fake_api.url]
    )
    out_ok = json.loads(capsys.readouterr().out)
    assert code_ok == 0 and out_ok["ok"] is True and out_ok["delivered"] == 1

    monkeypatch.setenv("CURSOR_CLOUD_HANDOFF_TEST_REPO", "Runner_Dashboard")
    monkeypatch.setenv("CURSOR_CLOUD_HANDOFF_TEST_BRANCH", "cursor/no-pr-3377")
    monkeypatch.delenv("CURSOR_CLOUD_HANDOFF_TEST_PR_NUMBER", raising=False)
    monkeypatch.setattr(cursor_cloud_handoff, "_detect_pr_number", lambda _root, _branch: None)

    code_stop = cursor_cloud_handoff.main(["stop"])
    _ = capsys.readouterr()
    assert code_stop == 0
    assert fake_api.last.body[0]["kind"] == "done"
    assert fake_api.last.body[0]["pr"] is None
