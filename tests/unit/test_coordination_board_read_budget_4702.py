"""#4702: one slow board read must not outlive the coordination response budget.

Root cause of the 35s+ lane stalls that #4706 only half fixed: the route now answers in
4s, but every RM ``agent_communicate`` call still ran under ``rm_scripts.DEFAULT_TIMEOUT``
(120 s). A stalled GitHub round-trip therefore kept a worker thread hostage ~30x longer
than any caller was willing to wait, so a 13-lane wave exhausts the threadpool and every
endpoint starts timing out together.

Verifies that:
1. Board *reads* are given a bounded upstream timeout, derived from the deadline the
   ``/sessions`` endpoint already honors, and board *writes* are never interrupted.
2. A read that hits that bound is reported as a structured, retryable degraded read.
3. When a last-known board snapshot exists it answers the timed-out read (rows plus an
   explicit incomplete-history warning) and still cannot confirm live presence.
4. ``GET /inbox`` — the unread half of the LANE_EVENT / Barb handoff path — responds
   inside the coordination budget instead of hanging.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from coordination import board as board_mod
from coordination import rm_scripts
from coordination import service as service_mod
from coordination.board import RMScriptError
from coordination.rm_scripts import ScriptResult
from routers import coordination

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_board_cache():
    board_mod.reset_cache()
    yield
    board_mod.reset_cache()


def _list_result(sessions: list[dict[str, Any]] | None = None) -> ScriptResult:
    """``agent_communicate list`` stdout shaped like the real board envelope."""
    return ScriptResult(
        0,
        json.dumps(
            {
                "ok": True,
                "complete": True,
                "sessions": sessions if sessions is not None else [],
                "messages": [],
                "conflicts": [],
                "warnings": [],
            }
        ),
        "",
    )


def _timeout_result() -> ScriptResult:
    """What ``run_argv`` returns when the RM script is killed at its timeout."""
    cmd = ["python", "-m", "scripts.agent_communicate", "--repo", "Repository_Management", "list"]
    return ScriptResult(130, "", str(subprocess.TimeoutExpired(cmd, board_mod.READ_TIMEOUT_SECONDS)))


def _presence_row(session: str = "qwen-ct-1", repo: str = "Runner_Dashboard") -> dict[str, Any]:
    expires = (datetime.now(UTC) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    return {
        "kind": "presence",
        "session": session,
        "repo": repo,
        "agent": "qwen",
        "issue": 4702,
        "branch": f"qwen-ct-1/{session}",
        "paths": ["backend/coordination/board.py"],
        "goals": {},
        "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "expires": expires,
    }


def _fake_run_module(results: dict[str, Any], seen: list[Any]):
    def fake(module: str, *args: str, **kwargs: Any) -> ScriptResult:
        seen.append((module, args, kwargs.get("timeout")))
        if module == "agent_communicate" and "send" in args:
            return ScriptResult(0, json.dumps({"ok": True, "receipt": "https://example.test/1"}), "")
        outcome = results.get(module)
        if callable(outcome):
            return outcome(args)
        return outcome if outcome is not None else _list_result()

    return fake


def test_board_reads_are_given_a_bounded_upstream_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every RM board read passes a timeout; the 120s default must no longer apply to reads."""
    seen: list[Any] = []
    monkeypatch.setattr(board_mod, "run_module", _fake_run_module({}, seen))

    board_mod.read_sessions()
    board_mod.read_inbox("qwen-ct-1", "Runner_Dashboard")

    assert len(seen) == 2, "expected exactly the list read and the inbox read"
    assert all(timeout == board_mod.READ_TIMEOUT_SECONDS for _module, _args, timeout in seen), seen


def test_all_repos_fallback_reads_are_bounded_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-repo fallback fan-out is bounded per script call, not just the ``--all-repos`` probe."""
    monkeypatch.setenv("COORDINATION_REPOS", "Tools")
    seen: list[Any] = []

    def fallback(args: tuple[str, ...]) -> ScriptResult:
        if "--all-repos" in args:
            return ScriptResult(2, "", "error: unrecognized arguments: --all-repos")
        return _list_result()

    monkeypatch.setattr(board_mod, "run_module", _fake_run_module({"agent_communicate": fallback}, seen))

    data = board_mod.read_sessions()

    assert data["available"] is True
    assert len(seen) == 3, "one probe plus one read per configured repo"
    assert all(timeout == board_mod.READ_TIMEOUT_SECONDS for _module, _args, timeout in seen), seen


def test_board_writes_are_never_interrupted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A board write keeps the unbudgeted default: killing ``gh api`` mid-send is how messages get lost."""
    seen: list[Any] = []
    monkeypatch.setattr(board_mod, "run_module", _fake_run_module({}, seen))

    board_mod.publish("Runner_Dashboard", "qwen-ct-1", "send", "--to=openbot-barb", "text")

    assert seen == [
        (
            "agent_communicate",
            ("--repo", "Runner_Dashboard", "--session", "qwen-ct-1", "send", "--to=openbot-barb", "text"),
            rm_scripts.DEFAULT_TIMEOUT,
        )
    ], seen


def test_read_budget_never_outlives_the_deadline_its_own_caller_honors() -> None:
    """The bound is derived, not invented: no read may outwait the endpoint that joins on it."""
    assert 0 < board_mod.READ_TIMEOUT_SECONDS < rm_scripts.DEFAULT_TIMEOUT
    assert board_mod.READ_TIMEOUT_SECONDS <= service_mod.SESSION_RESPONSE_DEADLINE_SECONDS


def test_script_result_distinguishes_a_timeout_from_a_failure_that_cannot_recover() -> None:
    timed_out = ScriptResult(130, "", str(subprocess.TimeoutExpired(["python"], 15)))
    assert timed_out.timed_out is True
    assert ScriptResult(130, "", "FileNotFoundError(2, 'No such file or directory')").timed_out is False
    assert ScriptResult(1, "", "Traceback: boom").timed_out is False
    assert ScriptResult(0, "{}", "").timed_out is False


def test_a_killed_script_reports_itself_as_timed_out(tmp_path: Path) -> None:
    """The bound is real: ``run_argv`` kills a script that overshoots it, and says so."""
    slow = tmp_path / "slow.py"
    slow.write_text("import time\n\ntime.sleep(5)\n", encoding="utf-8")

    result = rm_scripts.run_argv([sys.executable, str(slow)], tmp_path, timeout=0.2)

    assert result.rc == 130 and result.timed_out is True


def test_timed_out_board_read_is_a_structured_retryable_degraded_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(board_mod, "run_module", _fake_run_module({"agent_communicate": _timeout_result()}, []))

    data = board_mod.read_sessions()

    assert data["available"] is False
    assert data["complete"] is False
    assert data["retryable"] is True
    assert "timed out" in data["reason"], data["reason"]


def test_a_timed_out_read_falls_back_to_the_snapshot_without_confirming_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale rows beat an empty board, but never count as live presence (#4594 fail-open contract)."""
    live = {"board": True}

    def outcome(_args: tuple[str, ...]) -> ScriptResult:
        return _list_result([_presence_row()]) if live["board"] else _timeout_result()

    monkeypatch.setattr(board_mod, "run_module", _fake_run_module({"agent_communicate": outcome}, []))

    assert board_mod.read_sessions()["complete"] is True
    board_mod.publish("Runner_Dashboard", "qwen-ct-1", "send", "text")  # a write invalidates the cache
    live["board"] = False

    degraded = board_mod.read_sessions()

    assert degraded["available"] is True
    assert degraded["complete"] is False
    assert degraded["stale"] is True
    assert degraded["retryable"] is True
    assert degraded["sessions"][0]["session"] == "qwen-ct-1"
    assert any("board read failed" in str(w) for w in degraded["read_warnings"])
    with pytest.raises(RMScriptError):
        board_mod.has_live_session("qwen-ct-1", "Runner_Dashboard")


def test_inbox_route_responds_within_the_coordination_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3: slow upstream board I/O cannot stall the handoff read past the budget."""
    gate = threading.Event()
    monkeypatch.setattr(coordination, "COORDINATION_TIMEOUT", 0.05, raising=False)

    def slow(_session: str, _repo: str) -> dict[str, Any]:
        gate.wait(2)
        return {}

    monkeypatch.setattr(service_mod, "inbox", slow)

    async def call_and_verify() -> None:
        started = time.monotonic()
        try:
            with pytest.raises(coordination.HTTPException) as caught:
                await coordination.get_inbox(session="qwen-ct-1", repo="Runner_Dashboard", _peer="test")
            assert time.monotonic() - started < 1
            assert caught.value.status_code == 503
            assert caught.value.detail["retryable"] is True
        finally:
            gate.set()

    asyncio.run(call_and_verify())
