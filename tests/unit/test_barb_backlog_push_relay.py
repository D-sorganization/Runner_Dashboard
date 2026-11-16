"""Integration tests for barb_backlog_push_relay (#3711 extension, PR slice b)."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import barb_backlog_push_lib as lib  # noqa: E402
import barb_backlog_push_relay as relay  # noqa: E402

BOT = "d-sorgclaudeagent[bot]"
TODAY = "2026-11-06"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
VALID = {"repos": ["Runner_Dashboard"], "duration_min": 90, "lanes": 2}
BODY = "```backlog-push-request\n" + json.dumps(VALID) + "\n```"

# Pids these tests write into pid files and state. None of them is a process the test owns.
_FIXTURE_PIDS = frozenset({1000, 2000, 4300, 9999})


@pytest.fixture(autouse=True)
def _fixture_pids_are_not_host_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A liveness probe (signal 0) of a fixture pid never reaches the host's process table (#4393).

    ``_pid_identity_matches`` probes with ``os.kill(pid, 0)``. Where the host happened to run
    a process with one of these pids, "the supervisor is gone" tests saw it as still running
    (CI: ``test_recovery_across_restarts_distinguishes_outcome``, ``assert 0 == 1``). A test
    that needs a fixture pid alive patches the relay's helpers itself, which still wins.
    """
    real_kill = relay.os.kill

    def kill(pid: int, sig: int) -> None:
        if sig == 0 and pid in _FIXTURE_PIDS:
            raise ProcessLookupError(pid)
        real_kill(pid, sig)

    monkeypatch.setattr(relay.os, "kill", kill)


class FakeGitHub:
    def __init__(self, issues: list[dict[str, Any]], editor: str | None = None) -> None:
        self.issues, self.editor, self.writes = issues, editor, []

    def open_requests(self, repo: str, label: str) -> list[dict[str, Any]]:
        assert label == lib.REQUEST_LABEL
        return self.issues

    def snapshot(self, repo: str, number: int) -> dict[str, Any]:
        issue = next(i for i in self.issues if i["number"] == number)
        return {"author": issue["user"]["login"], "editor": self.editor, "body": issue["body"]}

    def comment(self, repo: str, number: int, body: str) -> None:
        self.writes.append(("comment", number, body))

    def label(self, repo: str, number: int, name: str) -> None:
        self.writes.append(("label", number, name))


def issue(number: int = 7, **extra: Any) -> dict[str, Any]:
    return {"number": number, "user": {"login": BOT}, "body": BODY, "labels": [], **extra}


def cfg(tmp_path: Path, cap: int = 2) -> lib.BacklogPushConfig:
    return lib.BacklogPushConfig(
        True,
        "http://hub.test",
        "tok",
        frozenset({BOT}),
        cap,
        tmp_path / "state.json",
        "D-sorganization/barbara",
    )


def test_relay_off_by_default_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAY_BACKLOG_PUSH_ENABLED", "0")
    assert relay.main([]) == 0


def test_starts_with_mock_launcher_and_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[dict[str, Any]] = []

    def fake_launcher(payload: dict[str, Any], log_dir: Path) -> dict[str, int]:
        launched.append(payload)
        return {"supervisor": 1000, "lane1": 1001, "lane2": 1002}

    monkeypatch.setattr(relay, "hub_resource_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(relay, "_pid_alive", lambda _pid: False)

    gh = FakeGitHub([issue()])
    state = {"processed": {}, "push_starts": {}, "active": None}
    c = cfg(tmp_path)
    first = relay.BacklogPushRelay(c, gh, state, launcher=fake_launcher, today=TODAY, now_fn=lambda: NOW)
    assert first.run()["started"] == 1
    assert ("label", 7, lib.STARTED) in gh.writes
    assert len(launched) == 1 and len(launched[0]["plan"]) == 3

    active = state["active"]
    assert active is not None
    log_dir = Path(str(active["log_dir"]))
    for session in [active["supervisor_session"], *active["lane_sessions"]]:
        (log_dir / f"{session}.exit").write_text("0", encoding="utf-8")

    second = relay.BacklogPushRelay(c, gh, state, today=TODAY, now_fn=lambda: NOW)
    assert second.run()["done"] == 1
    assert state["active"] is None


def test_dry_run_expired_active_does_not_terminate_or_clear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    terminated: list[dict[str, Any]] = []
    monkeypatch.setattr(relay, "_terminate_from_active", lambda active: terminated.append(active))
    state = {
        "processed": {"7": {"status": "running"}},
        "push_starts": {TODAY: 1},
        "active": {
            "issue": 7,
            "deadline_at": (NOW - timedelta(minutes=1)).isoformat(),
            "supervisor_session": "sup",
            "lane_sessions": ["lane1"],
            "pids": {"supervisor": 999},
            "log_dir": str(tmp_path / "logs"),
        },
    }
    gh = FakeGitHub([])
    counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, dry_run=True, now_fn=lambda: NOW).run()
    assert counts["done"] == 0
    assert state["active"] is not None
    assert terminated == []


def test_worker_failure_exit_finalizes_with_error_summary(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "barb-bpush-7-sup"
    (log_dir / f"{session}.pid").write_text("1000", encoding="utf-8")
    (log_dir / f"{session}.exit").write_text("37", encoding="utf-8")
    state = {
        "processed": {"7": {"status": "running"}},
        "push_starts": {TODAY: 1},
        "active": {
            "issue": 7,
            "deadline_at": (NOW + timedelta(hours=2)).isoformat(),
            "supervisor_session": session,
            "lane_sessions": [],
            "pids": {"supervisor": 1000},
            "log_dir": str(log_dir),
        },
    }
    gh = FakeGitHub([])
    counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, now_fn=lambda: NOW).run()
    assert counts["done"] == 1
    assert state["active"] is None
    assert state["processed"]["7"]["outcome"] == "failed"
    assert state["processed"]["7"]["exit_codes"] == {session: 37}
    comments = [w for w in gh.writes if w[0] == "comment"]
    assert comments and "status 37" in comments[-1][2]


def test_missing_exit_file_is_unknown_outcome_not_success(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "barb-bpush-7-sup"
    (log_dir / f"{session}.pid").write_text("1000", encoding="utf-8")
    state = {
        "processed": {"7": {"status": "running"}},
        "push_starts": {TODAY: 1},
        "active": {
            "issue": 7,
            "deadline_at": (NOW + timedelta(hours=2)).isoformat(),
            "supervisor_session": session,
            "lane_sessions": [],
            "pids": {"supervisor": 1000},
            "log_dir": str(log_dir),
        },
    }
    gh = FakeGitHub([])
    relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, now_fn=lambda: NOW).run()
    assert state["active"] is None
    assert state["processed"]["7"]["outcome"] == "unknown"
    assert state["processed"]["7"]["exit_codes"] == {session: None}
    comments = [w for w in gh.writes if w[0] == "comment"]
    assert comments and "without recording exit status" in comments[-1][2]


def test_launcher_failure_refuses_without_active_or_daily_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "hub_resource_refusal", lambda *_a, **_k: None)

    def fail_launcher(_payload: dict[str, Any], _log_dir: Path) -> dict[str, int]:
        raise RuntimeError("backlog-push spawn failed")

    gh = FakeGitHub([issue()])
    state = {"processed": {}, "push_starts": {}, "active": None}
    counts = relay.BacklogPushRelay(
        cfg(tmp_path), gh, state, launcher=fail_launcher, today=TODAY, now_fn=lambda: NOW
    ).run()
    assert counts["refused"] == 1 and counts["started"] == 0
    assert state["active"] is None and state["push_starts"] == {}


def test_default_launcher_spawn_subprocess_omits_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs.get("env", {}))

        class R:
            returncode = 0
            stdout = "{}"
            stderr = ""

        return R()

    monkeypatch.setattr(relay.subprocess, "run", fake_run)
    relay.BacklogPushRelay(cfg(Path("/tmp/x")), FakeGitHub([]), {}, today=TODAY)._default_launcher(
        {"plan": []}, Path("/tmp/logs")
    )
    assert "GH_TOKEN" not in captured and "GITHUB_TOKEN" not in captured


def test_resource_gate_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "hub_resource_refusal", lambda *_a, **_k: "Desk hub io_pressure 40.0%>=35%")
    gh = FakeGitHub([issue()])
    state = {"processed": {}, "push_starts": {}, "active": None}
    counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, now_fn=lambda: NOW).run()
    assert counts["refused"] == 1


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "hub_resource_refusal", lambda *_a, **_k: None)
    gh = FakeGitHub([issue()])
    state = {"processed": {}, "push_starts": {}, "active": None}
    relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, dry_run=True, now_fn=lambda: NOW).run()
    assert gh.writes == [] and state["active"] is None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="relay liveness probe os.kill(pid, 0) is CTRL_C_EVENT on Windows; relay deploys on POSIX only",
)
def test_active_push_not_finalized_while_detached_process_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        start_new_session=True,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "barb-bpush-7-test-sup"
    (log_dir / f"{session}.pid").write_text(str(sleeper.pid), encoding="utf-8")

    state = {
        "processed": {"7": {"status": "running"}},
        "push_starts": {TODAY: 1},
        "active": {
            "issue": 7,
            "deadline_at": (NOW + timedelta(hours=2)).isoformat(),
            "supervisor_session": session,
            "lane_sessions": [],
            "pids": {"supervisor": sleeper.pid},
            "log_dir": str(log_dir),
        },
    }
    gh = FakeGitHub([])
    counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, now_fn=lambda: NOW).run()
    assert counts["done"] == 0 and state["active"] is not None
    assert sleeper.poll() is None
    sleeper.kill()
    sleeper.wait(timeout=5)


def _finished_worker_state(tmp_path: Path, exit_code: str | None) -> dict[str, Any]:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "barb-bpush-7-sup"
    (log_dir / f"{session}.pid").write_text("1000", encoding="utf-8")
    if exit_code is not None:
        (log_dir / f"{session}.exit").write_text(exit_code, encoding="utf-8")
    return {
        "processed": {"7": {"status": "running"}},
        "push_starts": {TODAY: 1},
        "active": {
            "issue": 7,
            "deadline_at": (NOW + timedelta(hours=2)).isoformat(),
            "supervisor_session": session,
            "lane_sessions": [],
            "pids": {"supervisor": 1000},
            "log_dir": str(log_dir),
        },
    }


@pytest.mark.parametrize("exit_code", ["0", "37", None])
def test_dry_run_finished_worker_never_finalizes_or_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: str | None
) -> None:
    """#4102: every dry-run finalization path (clean, failed, unknown exit) is preview-only."""
    terminated: list[dict[str, Any]] = []
    monkeypatch.setattr(relay, "_terminate_from_active", lambda active: terminated.append(active))
    monkeypatch.setattr(relay, "_pid_alive", lambda pid: False)
    state = _finished_worker_state(tmp_path, exit_code)
    before = json.loads(json.dumps(state))
    gh = FakeGitHub([])
    counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, dry_run=True, now_fn=lambda: NOW).run()
    assert counts["done"] == 0
    assert state == before
    assert terminated == []
    assert gh.writes == []
    assert not (tmp_path / "state.json").exists()


def test_dry_run_finalize_is_a_noop_even_when_called_directly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "_terminate_from_active", lambda active: pytest.fail("dry-run must not terminate"))
    state = _finished_worker_state(tmp_path, "0")
    gh = FakeGitHub([])
    pusher = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, dry_run=True, now_fn=lambda: NOW)
    pusher._finalize(7, "summary", timed_out=True)
    assert state["active"] is not None
    assert pusher.counts["done"] == 0
    assert gh.writes == []


def test_real_expiry_signals_only_owned_processes_and_clears_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4102: non-dry-run expiry still cleans up, scoped to the pids this push recorded."""
    signalled_groups: list[int] = []
    signalled_pids: list[int] = []
    monkeypatch.setattr(relay.os, "getpgid", lambda pid: pid + 100000, raising=False)
    monkeypatch.setattr(relay.os, "killpg", lambda pgid, sig: signalled_groups.append(pgid - 100000), raising=False)
    monkeypatch.setattr(
        relay.os,
        "kill",
        lambda pid, sig: signalled_pids.append(pid) if sig == signal.SIGTERM else None,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "sup.pid").write_text("4300", encoding="utf-8")
    (log_dir / "stranger.pid").write_text("9999", encoding="utf-8")
    state = {
        "processed": {"7": {"status": "running"}},
        "push_starts": {TODAY: 1},
        "active": {
            "issue": 7,
            "deadline_at": (NOW - timedelta(minutes=1)).isoformat(),
            "supervisor_session": "sup",
            "lane_sessions": [],
            "pids": {"supervisor": 4200, "lane1": 4201, "unrelated": 9998},
            "log_dir": str(log_dir),
        },
    }
    gh = FakeGitHub([])
    counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, now_fn=lambda: NOW).run()
    assert counts["done"] == 1
    assert state["active"] is None
    assert signalled_groups == [4201]
    assert 4200 not in signalled_groups
    assert 4200 not in signalled_pids
    assert 9999 not in signalled_pids and 9998 not in signalled_groups
    assert any("wall-clock timeout" in w[2] for w in gh.writes if w[0] == "comment")


def test_clean_exit_records_completed_outcome_and_receipt(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "barb-bpush-7-sup"
    (log_dir / f"{session}.pid").write_text("1000", encoding="utf-8")
    (log_dir / f"{session}.exit").write_text("0", encoding="utf-8")
    state = {
        "processed": {"7": {"status": "running"}},
        "push_starts": {TODAY: 1},
        "active": {
            "issue": 7,
            "deadline_at": (NOW + timedelta(hours=2)).isoformat(),
            "supervisor_session": session,
            "lane_sessions": [],
            "pids": {"supervisor": 1000},
            "log_dir": str(log_dir),
        },
    }
    gh = FakeGitHub([])
    counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, now_fn=lambda: NOW).run()
    assert counts["done"] == 1
    assert state["active"] is None
    entry = state["processed"]["7"]
    assert entry["status"] == "done"
    assert entry["outcome"] == "completed"
    assert entry["exit_codes"] == {session: 0}
    receipt = log_dir / "outcome_receipt.json"
    assert receipt.is_file()
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_data["outcome"] == "completed"
    assert receipt_data["exit_codes"] == {session: 0}


def test_pid_disappearance_without_evidence_is_unknown_not_completed(tmp_path: Path) -> None:
    # No log_dir or empty sessions: PID disappearance alone is NOT completed
    state = {
        "processed": {"7": {"status": "running"}},
        "push_starts": {TODAY: 1},
        "active": {
            "issue": 7,
            "deadline_at": (NOW + timedelta(hours=2)).isoformat(),
            "supervisor_session": None,
            "lane_sessions": [],
            "pids": {"supervisor": 1000},
            "log_dir": None,
        },
    }
    gh = FakeGitHub([])
    counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, now_fn=lambda: NOW).run()
    assert counts["done"] == 1
    assert state["active"] is None
    entry = state["processed"]["7"]
    assert entry["outcome"] == "unknown"


@pytest.mark.parametrize(
    ("exit_val", "expected_outcome", "expected_summary_part"),
    [
        ("0", "completed", "all lane processes exited cleanly"),
        ("37", "failed", "status 37"),
        (None, "unknown", "without recording exit status"),
    ],
)
def test_recovery_across_restarts_distinguishes_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_val: str | None,
    expected_outcome: str,
    expected_summary_part: str,
) -> None:
    """#4104: across relay restart, loaded active state resolves correctly from exit evidence."""
    monkeypatch.setattr(relay, "_pid_alive", lambda _pid: False)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "barb-bpush-8-sup"
    (log_dir / f"{session}.pid").write_text("2000", encoding="utf-8")
    if exit_val is not None:
        (log_dir / f"{session}.exit").write_text(exit_val, encoding="utf-8")

    state_path = tmp_path / "state.json"
    saved_state = {
        "processed": {"8": {"status": "running", "supervisor_session": session}},
        "push_starts": {TODAY: 1},
        "active": {
            "issue": 8,
            "started_at": (NOW - timedelta(minutes=10)).isoformat(),
            "deadline_at": (NOW + timedelta(hours=1)).isoformat(),
            "supervisor_session": session,
            "lane_sessions": [],
            "pids": {"supervisor": 2000},
            "log_dir": str(log_dir),
            "duration_min": 60,
        },
    }
    relay.save_state(state_path, saved_state)

    # Simulate fresh relay process startup loading state from disk
    loaded_state = relay.load_state(state_path)
    gh = FakeGitHub([])
    c = lib.BacklogPushConfig(
        True, "http://hub.test", "tok", frozenset({BOT}), 2, state_path, "D-sorganization/barbara"
    )
    pusher = relay.BacklogPushRelay(c, gh, loaded_state, today=TODAY, now_fn=lambda: NOW)
    counts = pusher.run()

    assert counts["done"] == 1
    assert loaded_state["active"] is None
    entry = loaded_state["processed"]["8"]
    assert entry["status"] == "done"
    assert entry["outcome"] == expected_outcome
    expected_code = int(exit_val) if exit_val is not None else None
    assert entry["exit_codes"] == {session: expected_code}

    receipt_file = log_dir / "outcome_receipt.json"
    assert receipt_file.is_file()
    rcpt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert rcpt["outcome"] == expected_outcome
    assert rcpt["exit_codes"] == {session: expected_code}

    # State file on disk must reflect finalized state
    disk_state = relay.load_state(state_path)
    assert disk_state["active"] is None
    assert disk_state["processed"]["8"]["outcome"] == expected_outcome

    comments = [w for w in gh.writes if w[0] == "comment"]
    assert comments and expected_summary_part in comments[-1][2]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="relay liveness probe os.kill(pid, 0) is CTRL_C_EVENT on Windows; relay deploys on POSIX only",
)
def _verified_pid_record(pid: int, session: str) -> str:
    start_time = relay._get_process_start_time_ticks(pid)
    return json.dumps({"pid": pid, "start_time": start_time, "session": session})


def test_terminate_tree_refuses_malformed_pidfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Refs #4263: malformed pidfile must block group signal for the matching active pid."""
    signalled: list[int] = []
    monkeypatch.setattr(relay.os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(
        relay.os,
        "killpg",
        lambda _pgid, sig: signalled.append(sig) if sig == signal.SIGTERM else None,
        raising=False,
    )
    monkeypatch.setattr(relay.os, "kill", lambda *_a: signalled.append(-1))

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "barb-bpush-malformed-sup"
    (log_dir / f"{session}.pid").write_text("not-json-and-not-int", encoding="utf-8")
    active = {
        "supervisor_session": session,
        "lane_sessions": [],
        "log_dir": str(log_dir),
    }
    with caplog.at_level("WARNING"):
        relay._terminate_tree({"supervisor": 5100}, active)

    assert signalled == []
    assert "unverifiable" in caplog.text


def test_terminate_tree_refuses_pidfile_pid_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Refs #4263: pidfile naming a different pid than active state must not signal the state pid."""
    signalled_groups: list[int] = []
    monkeypatch.setattr(relay.os, "getpgid", lambda pid: pid + 100000, raising=False)
    monkeypatch.setattr(
        relay.os,
        "killpg",
        lambda pgid, sig: signalled_groups.append(pgid - 100000) if sig == signal.SIGTERM else None,
        raising=False,
    )

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "barb-bpush-mismatch-sup"
    (log_dir / f"{session}.pid").write_text("9999", encoding="utf-8")
    active = {
        "supervisor_session": session,
        "lane_sessions": [],
        "log_dir": str(log_dir),
    }
    with caplog.at_level("WARNING"):
        relay._terminate_tree({"supervisor": 5200}, active)

    assert signalled_groups == []
    assert "unverifiable" in caplog.text


def test_terminate_tree_signals_verified_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refs #4263: verified pidfile identity still allows group signal on timeout cleanup."""
    signalled_groups: list[int] = []
    monkeypatch.setattr(relay, "_pid_identity_matches", lambda *_a, **_k: True)
    monkeypatch.setattr(relay.os, "getpgid", lambda pid: pid + 100000, raising=False)
    monkeypatch.setattr(
        relay.os,
        "killpg",
        lambda pgid, sig: signalled_groups.append(pgid - 100000) if sig == signal.SIGTERM else None,
        raising=False,
    )

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "barb-bpush-verified-sup"
    pid = 5300
    (log_dir / f"{session}.pid").write_text(_verified_pid_record(pid, session), encoding="utf-8")
    active = {
        "supervisor_session": session,
        "lane_sessions": [],
        "log_dir": str(log_dir),
    }
    relay._terminate_tree({"supervisor": pid}, active)

    assert signalled_groups == [pid]


def test_unrelated_live_process_in_pidfile_is_not_alive_and_never_signalled(tmp_path: Path) -> None:
    """#4263: a pidfile pointing at an unrelated live process must be reported not-alive.

    This simulates the scenario after a reboot where a PID is recycled to a different process.
    The test verifies that even though the process is alive, the relay does not signal it
    because the identity (start_time and session token) do not match.
    """
    # Spawn a long-running process that will live for the duration of the test
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        start_new_session=True,
    )

    # Create a pidfile pointing at the unrelated sleeper process
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "barb-bpush-7-sup"

    # Write the sleeper's pid to the pidfile with a mismatched start_time
    # This simulates a stale pidfile from before the reboot that now points to an unrelated process
    pidfile = log_dir / f"{session}.pid"
    pidfile.write_text(
        json.dumps({"pid": sleeper.pid, "start_time": 100, "session": session}),
        encoding="utf-8",
    )

    state = {
        "processed": {"7": {"status": "running"}},
        "push_starts": {TODAY: 1},
        "active": {
            "issue": 7,
            "deadline_at": (NOW - timedelta(minutes=1)).isoformat(),  # expired
            "supervisor_session": session,
            "lane_sessions": [],
            "pids": {"supervisor": sleeper.pid},
            "log_dir": str(log_dir),
        },
    }

    # Track what processes get signalled
    signalled_pids: list[int] = []
    original_kill = relay.os.kill

    def mock_kill(pid: int, sig: int) -> None:
        if sig == signal.SIGTERM:
            signalled_pids.append(pid)
            raise OSError("not signalling test process")
        original_kill(pid, sig)

    # Patch os.kill to track calls
    import unittest.mock as mock

    with mock.patch.object(relay.os, "kill", side_effect=mock_kill):
        gh = FakeGitHub([])
        counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, now_fn=lambda: NOW).run()

    # The relay should finalize the expired active without signalling the unrelated process
    assert counts["done"] == 1
    assert state["active"] is None

    # Verify the unrelated process was never signalled
    assert sleeper.pid not in signalled_pids
    assert sleeper.poll() is None  # Process should still be alive

    # Verify the pidfile was removed since the process didn't match the recorded identity
    assert not pidfile.exists()

    sleeper.kill()
    sleeper.wait(timeout=5)
