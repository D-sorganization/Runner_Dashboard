"""Unit tests for staff runner_ops helper functions (#1586, #1587, #1588, #1593)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from staff.runner_ops import (
    pump_output,
    resolve_launch_paths,
    sanitize_launch_env,
    select_first_available_provider,
)
from staff.store import RunRecord, RunStore


class DummyAdapter:
    def __init__(
        self,
        provider_id: str = "claude",
        installed: bool = True,
        unattended: bool = True,
        argv: tuple[str, ...] = ("bin",),
        policy_text: Any = None,
    ) -> None:
        self.provider_id = provider_id
        self._installed = installed
        self.unattended = unattended
        self.argv = argv
        if policy_text is not None:
            self.policy_text = policy_text

    def installed(self) -> bool:
        return self._installed

    def parse_line(self, line: str) -> dict[str, Any]:
        return {"kind": "text", "text": line.strip()}


@pytest.mark.unit
def test_select_first_available_provider_empty() -> None:
    assert select_first_available_provider({}, ()) == "claude"


@pytest.mark.unit
def test_select_first_available_provider_selection() -> None:
    adapters = {
        "claude": DummyAdapter("claude", installed=True),
        "codex": DummyAdapter("codex", installed=True),
    }
    assert select_first_available_provider(adapters, ("claude", "codex")) == "claude"
    assert select_first_available_provider(adapters, ("codex", "claude")) == "codex"


@pytest.mark.unit
def test_resolve_launch_paths_no_placeholders(tmp_path: Path) -> None:
    adapter = DummyAdapter("claude", argv=("run", "arg"))
    paths = resolve_launch_paths(adapter, tmp_path)  # type: ignore[arg-type]
    assert paths == {}


@pytest.mark.unit
def test_resolve_launch_paths_gitdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = DummyAdapter("codex", argv=("run", "--dir", "{gitdir}"))
    monkeypatch.setattr("staff.workspace.git_common_dir", lambda p: p / ".git")
    paths = resolve_launch_paths(adapter, tmp_path)  # type: ignore[arg-type]
    assert paths == {"gitdir": str(tmp_path / ".git")}


@pytest.mark.unit
def test_pump_output_streams_and_extracts_result(tmp_path: Path) -> None:
    rec = RunRecord(
        id="run-test123",
        role="worker",
        provider="claude",
        model="claude-3-7-sonnet",
        machine="testbox",
        repo="D-sorganization/Runner_Dashboard",
        target_kind="issue",
        target_ref="1593",
        prompt="fix it",
    )
    adapter = DummyAdapter("claude")
    transcript = tmp_path / "transcript.log"
    store = MagicMock(spec=RunStore)

    proc = MagicMock()
    proc.stdout = ["hello agent\n", "STAFF_RESULT: done successfully\n"]

    usage, result = pump_output(rec, adapter, proc, transcript, store)  # type: ignore[arg-type]

    assert result == "STAFF_RESULT: done successfully"
    assert transcript.exists()
    content = transcript.read_text(encoding="utf-8")
    assert "hello agent" in content
    assert "STAFF_RESULT: done successfully" in content
    assert store.append_event.call_count == 2


@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity", "cursor-agent"])
def test_sanitize_launch_env_strips_provider_keys_for_every_other_provider(provider: str) -> None:
    """#3534: only the anthropic-api launch itself may ever see a provider API key."""
    env = {
        "PATH": "/usr/bin",
        "ANTHROPIC_API_KEY": "sk-secret",  # pragma: allowlist secret
        "OPENAI_API_KEY": "sk-openai",  # pragma: allowlist secret
    }
    cleaned = sanitize_launch_env(env, execution_mode="local", provider=provider)
    assert "ANTHROPIC_API_KEY" not in cleaned
    assert "OPENAI_API_KEY" not in cleaned
    assert cleaned["PATH"] == "/usr/bin"


def test_sanitize_launch_env_keeps_key_for_anthropic_api_local_launch() -> None:
    env = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-secret"}  # pragma: allowlist secret
    cleaned = sanitize_launch_env(env, execution_mode="local", provider="anthropic-api")
    assert cleaned["ANTHROPIC_API_KEY"] == "sk-secret"  # pragma: allowlist secret


def test_sanitize_launch_env_strips_key_for_anthropic_api_cloud_launch() -> None:
    """Cloud dispatch never gets a key, even for the anthropic-api provider itself (#2627 invariant)."""
    env = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-secret"}  # pragma: allowlist secret
    cleaned = sanitize_launch_env(env, execution_mode="cloud", provider="anthropic-api")
    assert "ANTHROPIC_API_KEY" not in cleaned
