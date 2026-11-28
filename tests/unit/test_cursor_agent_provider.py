"""Cursor-agent CLI provider: OS sandbox, models, and fleet git identity."""

from __future__ import annotations

import sys

import pytest
from staff.git_identity import staff_git_user_email, staff_git_user_name

from staff import adapters as adapters_mod


@pytest.mark.unit
def test_cursor_agent_sandbox_disabled_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    argv = adapters_mod.ADAPTERS["cursor-agent"].build_command("fix it", "/tmp/wt", model="composer-2.5")
    assert argv[argv.index("--sandbox") + 1] == "disabled"


@pytest.mark.unit
def test_cursor_agent_sandbox_enabled_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    argv = adapters_mod.ADAPTERS["cursor-agent"].build_command("fix it", "/tmp/wt", model="composer-2.5")
    assert argv[argv.index("--sandbox") + 1] == "enabled"


@pytest.mark.unit
def test_cursor_agent_default_model_is_grok_4_7_high() -> None:
    argv = adapters_mod.ADAPTERS["cursor-agent"].build_command("fix it", "/tmp/wt", model=None)
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "grok-4.7-high"
    assert "--trust" in argv


@pytest.mark.unit
def test_cursor_agent_runtime_env_uses_fleet_bot_git_identity() -> None:
    env = adapters_mod.ADAPTERS["cursor-agent"].runtime_env()
    assert env["GIT_AUTHOR_NAME"] == staff_git_user_name()
    assert env["GIT_AUTHOR_EMAIL"] == staff_git_user_email()
    assert env["GIT_COMMITTER_EMAIL"] == staff_git_user_email()


@pytest.mark.unit
def test_cursor_agent_models_exclude_stale_ids() -> None:
    from agent_remediation.provider_registry import _CURSOR_AGENT_MODELS

    models = set(_CURSOR_AGENT_MODELS)
    assert "composer-2.5" in models
    assert "auto" in models
    assert "claude-fable-5-2-high" in models
    assert "claude-fable-5-2" not in models
    for stale in ("gpt-5", "claude-sonnet-4", "grok-code-fast-1", "grok-4"):
        assert stale not in models
