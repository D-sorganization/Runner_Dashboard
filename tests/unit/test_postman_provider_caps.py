"""Provider messaging capability probe for the postman relay (#3234)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts import postman_provider_caps as caps


@pytest.fixture
def secret_env() -> dict[str, str]:
    return {
        "CLAUDE_CODE_SSE_PORT": "18432",
        "CLAUDE_CODE_OAUTH_TOKEN": "ghp_" + "A" * 36,
        "FLEET_API_TOKEN": "svc_" + "B" * 40,
    }


def test_claude_push_when_sse_port_set(tmp_path: Path, secret_env: dict[str, str]) -> None:
    cfg = tmp_path / "claude"
    cfg.mkdir()
    report = caps.probe_providers(env=secret_env, claude_config_dir=cfg)
    claude = report["providers"]["claude_code"]
    assert claude["delivery"] == "push"
    assert any("CLAUDE_CODE_SSE_PORT" in item for item in claude["evidence"])


def test_claude_push_when_channels_enabled_in_settings(tmp_path: Path) -> None:
    cfg = tmp_path / "claude"
    cfg.mkdir()
    (cfg / "settings.json").write_text(
        json.dumps({"channels": {"enabled": True}}),
        encoding="utf-8",
    )
    report = caps.probe_providers(env={}, claude_config_dir=cfg)
    assert report["providers"]["claude_code"]["delivery"] == "push"
    assert any("channels" in item for item in report["providers"]["claude_code"]["evidence"])


def test_claude_poll_when_no_push_signals(tmp_path: Path) -> None:
    cfg = tmp_path / "claude"
    cfg.mkdir()
    (cfg / "settings.json").write_text("{}", encoding="utf-8")
    report = caps.probe_providers(env={}, claude_config_dir=cfg)
    assert report["providers"]["claude_code"]["delivery"] == "poll"


def test_codex_hook_when_user_prompt_submit_configured(tmp_path: Path) -> None:
    hooks = tmp_path / "codex" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [{"command": "python3 relay-inject.py"}],
                    "Stop": [],
                }
            }
        ),
        encoding="utf-8",
    )
    report = caps.probe_providers(env={}, codex_home=hooks.parent)
    codex = report["providers"]["codex_cli"]
    assert codex["delivery"] == "hook"
    assert any("UserPromptSubmit" in item for item in codex["evidence"])


def test_codex_hook_when_stop_only(tmp_path: Path) -> None:
    hooks = tmp_path / "codex" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(
        json.dumps({"hooks": {"Stop": [{"command": "python3 relay-poll-bridge.py"}]}}),
        encoding="utf-8",
    )
    report = caps.probe_providers(env={}, codex_home=hooks.parent)
    assert report["providers"]["codex_cli"]["delivery"] == "hook"


def test_codex_poll_without_hooks(tmp_path: Path) -> None:
    hooks = tmp_path / "codex" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(json.dumps({"hooks": {"Stop": []}}), encoding="utf-8")
    report = caps.probe_providers(env={}, codex_home=hooks.parent)
    assert report["providers"]["codex_cli"]["delivery"] == "poll"


def test_agy_hook_from_gemini_settings(tmp_path: Path) -> None:
    gemini = tmp_path / "gemini"
    gemini.mkdir()
    (gemini / "settings.json").write_text(
        json.dumps({"hooks": {"AfterAgent": [{"command": "python3 agy-relay.py"}]}}),
        encoding="utf-8",
    )
    report = caps.probe_providers(env={"ANTIGRAVITY_HOME": str(gemini)})
    assert report["providers"]["agy"]["delivery"] == "hook"


def test_agy_poll_without_hooks(tmp_path: Path) -> None:
    gemini = tmp_path / "gemini"
    gemini.mkdir()
    (gemini / "settings.json").write_text("{}", encoding="utf-8")
    report = caps.probe_providers(env={"ANTIGRAVITY_HOME": str(gemini)})
    assert report["providers"]["agy"]["delivery"] == "poll"


def test_report_never_emits_secret_values(secret_env: dict[str, str], tmp_path: Path) -> None:
    cfg = tmp_path / "claude"
    cfg.mkdir()
    raw = json.dumps(caps.probe_providers(env=secret_env, claude_config_dir=cfg))
    assert "ghp_" not in raw
    assert secret_env["CLAUDE_CODE_OAUTH_TOKEN"] not in raw
    assert secret_env["FLEET_API_TOKEN"] not in raw
    assert not re.search(r"\bghp_[A-Za-z0-9_]{20,}\b", raw)


def test_redaction_fixture_scrubs_token_like_evidence() -> None:
    dirty = "env file mentioned ghp_" + "x" * 36
    clean = caps.sanitize_text(dirty)
    assert "ghp_" not in clean
    assert "[REDACTED_GITHUB_TOKEN]" in clean


def test_probe_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "claude"
    cfg.mkdir()
    settings = cfg / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    before = settings.read_text(encoding="utf-8")

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("write blocked")

    monkeypatch.setattr(Path, "write_text", fail_write)
    caps.probe_providers(env={}, claude_config_dir=cfg)
    assert settings.read_text(encoding="utf-8") == before
