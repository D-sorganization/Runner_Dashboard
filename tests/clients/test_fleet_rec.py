"""TDD tests for Activity T2 ``fleet-rec`` node recorder for Linux/WSL CLI sessions (#2868)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fleet_client import FleetAPIError, FleetArgumentError, FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # noqa: F401
from fleet_rec import flush_recorder_spool, redact_node_stream, run_pty_process, run_recorded_cli


def test_opt_in_disabled_by_default_passes_through_without_hub_or_spool(
    fake_api: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FLEET_REC_ENABLED", raising=False)
    spool, ran = tmp_path / "rec-spool.json", []

    def passthrough(argv: list[str]) -> int:
        ran.append(list(argv))
        return 7

    rc = run_recorded_cli(["claude", "--print"], url=fake_api.url, spool_file=spool, passthrough_runner=passthrough)
    assert rc == 7 and ran == [["claude", "--print"]] and fake_api.requests == [] and not spool.exists()


def test_registers_session_before_launch_streams_chunks_and_emits_end_event(
    fake_api: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLEET_REC_ENABLED", "1")
    monkeypatch.setenv("FLEET_NODE", "wsl-node-01")
    spool, sid = tmp_path / "rec-spool.json", "claude-wsl-node-01-20261029-aa11bb"
    fake_api.respond("POST", "/api/v1/activity/events", 200, [{"ok": True}])
    fake_api.respond("POST", f"/api/v1/activity/sessions/{sid}/chunks", 200, {"ok": True})

    def pty_run(argv: list[str], on_output: Any) -> int:
        assert len(fake_api.requests) == 1 and fake_api.requests[0].body[0]["kind"] == "session_start"
        on_output(b"\x1b[32m working on task \x1b[0m\r\n")
        return 23

    kw = {"url": fake_api.url, "session": sid, "repo": "Runner_Dashboard", "issue": 2868, "spool_file": spool}
    assert run_recorded_cli(["claude", "--resume"], pty_runner=pty_run, **kw) == 23 and len(fake_api.requests) == 4
    evs = [fake_api.requests[i].body[0] for i in (0, 1, 3)]
    assert [(e["kind"], e["seq"]) for e in evs] == [("session_start", 0), ("chunk", 1), ("session_end", 2)]
    assert fake_api.requests[2].body["seq"] == 1 and "\x1b[32m" in fake_api.requests[2].body["data"]
    assert evs[2]["payload"] == {"cli": "claude", "exit_code": 23}


def test_node_side_redaction_runs_before_spooling_and_preserves_ansi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = tmp_path / "rec-spool.json"
    secret_tok, secret_key = "ghp_" + "A" * 36, "sk-ant-" + "B" * 24
    raw_line = f"\x1b[31mToken={secret_tok} Key={secret_key} Bearer {'C' * 24}\x1b[0m\n"
    redacted = redact_node_stream(raw_line)
    assert secret_tok not in redacted and secret_key not in redacted
    assert "[REDACTED_GITHUB_TOKEN]" in redacted and "[REDACTED_API_KEY]" in redacted and "\x1b[31m" in redacted

    def offline(self: Any, *a: Any, **kw: Any) -> Any:
        raise FleetAPIError(0, {"error": "offline"})

    monkeypatch.setattr(FleetClient, "request", offline)
    kw = {"url": "http://127.0.0.1:9", "enabled": True, "session": "codex-n-1", "spool_file": spool, "max_attempts": 1}
    rc = run_recorded_cli(["codex", "exec"], pty_runner=lambda argv, cb: (cb(raw_line.encode("utf-8")), 0)[1], **kw)
    spool_raw = spool.read_text(encoding="utf-8")
    assert rc == 0 and secret_tok not in spool_raw and secret_key not in spool_raw


def test_bounded_local_spool_emits_overflow_gap_and_replays_on_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool, sid = tmp_path / "rec-spool.json", "antigravity-node-20261029-ee33ff"

    def offline(self: Any, *a: Any, **kw: Any) -> Any:
        raise FleetAPIError(0, {"error": "offline"})

    monkeypatch.setattr(FleetClient, "request", offline)
    kw = {"url": "http://127.0.0.1:9", "enabled": True, "session": sid, "spool_file": spool, "max_spool_chunks": 2}
    rc = run_recorded_cli(
        ["agy"], max_attempts=1, pty_runner=lambda a, cb: ([cb(f"c-{i}\n".encode()) for i in range(1, 5)], 0)[1], **kw
    )
    saved = json.loads(spool.read_text(encoding="utf-8"))
    assert rc == 0 and [c["seq"] for c in saved["chunks"]] == [3, 4]
    gaps = [ev for ev in saved["events"] if ev["kind"] == "OverflowGap"]
    assert len(gaps) == 1 and gaps[0]["payload"]["dropped_chunks"] == 2

    delivered: list[tuple[str, Any]] = []

    def ok_req(self: Any, method: str, path: str, **kw: Any) -> dict[str, Any]:
        delivered.append((path, kw.get("body")))
        return {"ok": True}

    monkeypatch.setattr(FleetClient, "request", ok_req)
    summary = flush_recorder_spool(FleetClient("http://127.0.0.1:8321", agent="antigravity", session=sid), spool)
    assert summary["ok"] is True and summary["pending_chunks"] == 0 and summary["pending_events"] == 0
    assert [b["seq"] for path, b in delivered if path.endswith("/chunks")] == [3, 4]
    sent_kinds = [ev["kind"] for path, b in delivered if path == "/api/v1/activity/events" for ev in b]
    assert {"session_start", "OverflowGap", "chunk", "session_end"}.issubset(sent_kinds)


def test_real_pty_preserves_interactivity_tty_and_exit_code() -> None:
    captured: list[bytes] = []
    py = "import os,sys;l=sys.stdin.readline().strip();print(f'TTY={os.isatty(0)},{os.isatty(1)} GOT={l}');sys.exit(19)"
    rc = run_pty_process([sys.executable, "-c", py], on_output=captured.append, initial_input=b"ping-pty\n")
    combined = b"".join(captured).decode("utf-8", errors="replace")
    assert rc == 19 and "TTY=True,True" in combined and "GOT=ping-pty" in combined


def test_dbc_rejects_unsupported_cli_and_corrupt_spool(tmp_path: Path) -> None:
    with pytest.raises(FleetArgumentError, match="unsupported CLI"):
        run_recorded_cli(["bash", "-c", "echo no"], enabled=True)
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not-json", encoding="utf-8")
    with pytest.raises(FleetArgumentError, match="spool"):
        run_recorded_cli(["cursor-agent"], enabled=True, spool_file=corrupt)
