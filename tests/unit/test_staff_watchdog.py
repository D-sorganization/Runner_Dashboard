"""Tests for staff run watchdog, idle timeout, and process group cleanup (issue #1294, SC-A5)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest
from fleet_events import get_event_store
from staff import adapters as adapters_mod
from staff import roles as roles_mod
from staff import runner as runner_mod
from staff import store as store_mod
from staff.watchdog import StaffWatchdog, terminate_process_group

_SILENT_HANG_CLI = """
import sys, time
# Sleep silently without output
time.sleep(30)
"""

_CHATTY_OVERRUN_CLI = """
import sys, time
# Keep printing past any deadline
while True:
    print("chatty line", flush=True)
    time.sleep(0.05)
"""

_GRANDCHILD_SPAWNER_CLI = """
import json, subprocess, sys, time
# Spawn a grandchild background process that sleeps
child_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
print(json.dumps({"grandchild_pid": child_proc.pid}), flush=True)
# Keep parent alive waiting or sleeping
time.sleep(30)
"""


@pytest.fixture
def fake_cli_scripts(tmp_path: Path) -> dict[str, Path]:
    silent = tmp_path / "silent_cli.py"
    silent.write_text(_SILENT_HANG_CLI, encoding="utf-8")

    chatty = tmp_path / "chatty_cli.py"
    chatty.write_text(_CHATTY_OVERRUN_CLI, encoding="utf-8")

    spawner = tmp_path / "spawner_cli.py"
    spawner.write_text(_GRANDCHILD_SPAWNER_CLI, encoding="utf-8")

    return {"silent": silent, "chatty": chatty, "spawner": spawner}


@pytest.fixture
def role_dir(tmp_path: Path) -> Path:
    d = tmp_path / "roles"
    d.mkdir()
    (d / "watchdog-role.yml").write_text(
        "\n".join(
            [
                "name: watchdog-role",
                "title: Watchdog Role",
                "providers: [fake-silent, fake-chatty, fake-spawner]",
                "budget:",
                "  max_minutes: 0.00833",  # ~0.5s in minutes
                "  idle_minutes: 0.005",  # ~0.3s in minutes
                "permissions: {lease: false}",
                "surface: dashboard",
            ]
        ),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def staff_runner(
    tmp_path: Path,
    fake_cli_scripts: dict[str, Path],
    role_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[runner_mod.StaffRunner]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(role_dir))
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))

    adapters = {
        **adapters_mod.ADAPTERS,
        "fake-silent": adapters_mod.ProviderAdapter(
            provider_id="fake-silent",
            label="Fake Silent",
            executable=sys.executable,
            argv=(str(fake_cli_scripts["silent"]), "{prompt}"),
            json_lines=False,
        ),
        "fake-chatty": adapters_mod.ProviderAdapter(
            provider_id="fake-chatty",
            label="Fake Chatty",
            executable=sys.executable,
            argv=(str(fake_cli_scripts["chatty"]), "{prompt}"),
            json_lines=False,
        ),
        "fake-spawner": adapters_mod.ProviderAdapter(
            provider_id="fake-spawner",
            label="Fake Spawner",
            executable=sys.executable,
            argv=(str(fake_cli_scripts["spawner"]), "{prompt}"),
            json_lines=False,
        ),
    }
    monkeypatch.setattr(adapters_mod, "ADAPTERS", adapters)
    store_mod.reset_store()
    runner_mod.reset_runner()
    r = runner_mod.StaffRunner(
        store=store_mod.RunStore(tmp_path / "runs.sqlite3"),
        adapters=adapters,
        machine="TestNode",
    )
    monkeypatch.setattr(runner_mod, "_runner", r)
    yield r
    store_mod.reset_store()
    runner_mod.reset_runner()


def _wait_for_terminal(store: store_mod.RunStore, run_id: str, timeout: float = 10.0) -> store_mod.RunRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = store.get_run(run_id)
        assert rec is not None
        if rec.status in ("succeeded", "failed", "cancelled"):
            return rec
        time.sleep(0.05)
    rec = store.get_run(run_id)
    raise AssertionError(f"run {run_id} did not terminate; last status: {rec.status if rec else None}")


@pytest.mark.unit
def test_role_spec_budget_defaults_and_parsing() -> None:
    spec_default = roles_mod.parse_role({"name": "test-role"})
    assert spec_default.budget_max_minutes == 240.0
    assert spec_default.idle_minutes == 20.0

    spec_custom = roles_mod.parse_role(
        {
            "name": "custom-role",
            "budget": {"max_minutes": 120, "idle_minutes": 15},
        }
    )
    assert spec_custom.budget_max_minutes == 120.0
    assert spec_custom.idle_minutes == 15.0
    assert spec_custom.to_dict()["budget"]["max_minutes"] == 120.0
    assert spec_custom.to_dict()["budget"]["idle_minutes"] == 15.0


@pytest.mark.unit
def test_terminate_process_group_kills_parent_and_descendants() -> None:
    # Spawn parent which spawns grandchild
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time; "
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "print(c.pid, flush=True); "
            "time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert parent.stdout is not None
    grandchild_pid_str = parent.stdout.readline().strip()
    grandchild_pid = int(grandchild_pid_str)

    assert psutil.pid_exists(parent.pid)
    assert psutil.pid_exists(grandchild_pid)

    # Terminate process group
    ok = terminate_process_group(parent.pid, grace_period=1.0)
    assert ok is True

    # Both parent and grandchild should be gone
    time.sleep(0.5)
    assert not psutil.pid_exists(parent.pid)
    assert not psutil.pid_exists(grandchild_pid)


@pytest.mark.integration
def test_watchdog_kills_silent_cli_after_idle_timeout(
    staff_runner: runner_mod.StaffRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force idle timeout of 0.3s
    monkeypatch.setenv("STAFF_IDLE_TIMEOUT_SECONDS", "0.3")
    monkeypatch.setenv("STAFF_RUN_TIMEOUT_SECONDS", "30.0")

    rec = staff_runner.submit(runner_mod.RunRequest(role="watchdog-role", provider="fake-silent", prompt="test silent"))
    done = _wait_for_terminal(staff_runner.store, rec.id, timeout=5.0)

    assert done.status == "failed"
    assert done.failure_class == "stalled"
    assert "stalled" in done.error.lower()

    events = staff_runner.store.events_after(rec.id, 0)
    kinds = [e["kind"] for e in events]
    assert "stalled" in kinds


@pytest.mark.integration
def test_watchdog_kills_chatty_cli_after_wall_clock_timeout(
    staff_runner: runner_mod.StaffRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force wall-clock timeout of 0.4s and long idle timeout
    monkeypatch.setenv("STAFF_RUN_TIMEOUT_SECONDS", "0.4")
    monkeypatch.setenv("STAFF_IDLE_TIMEOUT_SECONDS", "30.0")

    rec = staff_runner.submit(runner_mod.RunRequest(role="watchdog-role", provider="fake-chatty", prompt="test chatty"))
    done = _wait_for_terminal(staff_runner.store, rec.id, timeout=5.0)

    assert done.status == "failed"
    assert done.failure_class == "timeout"
    assert "wall-clock limit" in done.error.lower() or "timeout" in done.error.lower()

    events = staff_runner.store.events_after(rec.id, 0)
    kinds = [e["kind"] for e in events]
    assert "timeout" in kinds


@pytest.mark.integration
def test_watchdog_kills_grandchild_process(
    staff_runner: runner_mod.StaffRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Grandchild spawns and prints PID, then parent sleeps silently -> triggers idle timeout
    monkeypatch.setenv("STAFF_IDLE_TIMEOUT_SECONDS", "0.4")
    monkeypatch.setenv("STAFF_RUN_TIMEOUT_SECONDS", "30.0")

    rec = staff_runner.submit(
        runner_mod.RunRequest(role="watchdog-role", provider="fake-spawner", prompt="test spawner")
    )
    done = _wait_for_terminal(staff_runner.store, rec.id, timeout=5.0)

    assert done.status == "failed"
    assert done.failure_class == "stalled"

    # Verify grandchild PID extracted from transcript or events was killed
    events = staff_runner.store.events_after(rec.id, 0)
    grandchild_pids = []
    for e in events:
        if "grandchild_pid" in e["text"]:
            data = json.loads(e["text"])
            grandchild_pids.append(data["grandchild_pid"])

    assert len(grandchild_pids) == 1
    grandchild_pid = grandchild_pids[0]
    time.sleep(0.5)
    assert not psutil.pid_exists(grandchild_pid)


@pytest.mark.unit
def test_watchdog_emits_heartbeat_periodically(tmp_path: Path) -> None:
    store = store_mod.RunStore(tmp_path / "heartbeat.sqlite3")
    rec = store_mod.RunRecord(
        id="run-hb-1",
        role="test",
        provider="p",
        model=None,
        machine="m",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="hb",
        status="running",
    )
    store.create_run(rec)

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
    try:
        watchdog = StaffWatchdog(
            run_id=rec.id,
            proc=proc,
            store=store,
            max_seconds=5.0,
            idle_seconds=5.0,
            heartbeat_interval=0.1,  # Fast heartbeats for testing
            check_interval=0.05,
        )
        watchdog.start()
        time.sleep(0.35)
        watchdog.stop()
    finally:
        proc.kill()
        proc.wait()

    events = store.events_after(rec.id, 0)
    heartbeats = [e for e in events if e["kind"] == "heartbeat"]
    assert len(heartbeats) >= 2
    for hb in heartbeats:
        data = json.loads(hb["text"])
        assert "last_output_at" in data
        assert "elapsed" in data
        assert data["elapsed"] > 0
    store.close()


@pytest.mark.unit
def test_watchdog_marks_unkillable_on_kill_failure(tmp_path: Path) -> None:
    store = store_mod.RunStore(tmp_path / "unkillable.sqlite3")
    rec = store_mod.RunRecord(
        id="run-unkillable-1",
        role="test",
        provider="p",
        model=None,
        machine="m",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="unkillable",
        status="running",
    )
    store.create_run(rec)

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    fleet_event_store = get_event_store()

    try:
        with patch("staff.watchdog.terminate_process_group", return_value=False):
            watchdog = StaffWatchdog(
                run_id=rec.id,
                proc=proc,
                store=store,
                max_seconds=0.1,  # Trigger immediately
                idle_seconds=5.0,
                check_interval=0.05,
            )
            watchdog.start()
            time.sleep(0.3)
            watchdog.stop()

            assert watchdog.failure_class == "unkillable"
            updated = store.get_run(rec.id)
            assert updated is not None
            assert updated.status == "failed"
            assert updated.failure_class == "unkillable"
            assert "unkillable" in updated.error

            events = store.events_after(rec.id, 0)
            err_events = [e for e in events if e["kind"] == "error"]
            assert len(err_events) > 0
            assert "unkillable" in err_events[0]["text"]

            # Verify critical fleet event
            fleet_events = fleet_event_store.recent(10)
            critical = [e for e in fleet_events if e.severity == "critical" and "unkillable" in e.detail]
            assert len(critical) > 0
    finally:
        proc.kill()
        proc.wait()
        store.close()
