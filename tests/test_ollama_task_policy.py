"""The SYSTEM task must not depend on the installing user's execution policy."""

from pathlib import Path


def test_bridge_task_sets_only_process_execution_policy() -> None:
    script = Path(__file__).resolve().parents[1] / "deploy/windows/ollama-wsl-bridge.ps1"
    source = script.read_text(encoding="utf-8")
    action = next(line for line in source.splitlines() if "$action = New-ScheduledTaskAction" in line)
    assert "-ExecutionPolicy RemoteSigned" in action
    assert "-NonInteractive" in action and "-WindowStyle Hidden" in action
    assert "Set-ExecutionPolicy" not in source
    assert "-ExecutionPolicy Bypass" not in source
