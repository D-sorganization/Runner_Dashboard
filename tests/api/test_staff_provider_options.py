"""Cursor Agent and Ollama provider options for Staff Hub runs (issue #1252).

Shapes and flags were captured from the installed CLIs on DeskComputer WSL on
2026-09-23: cursor-agent 2026.09.18, codex 0.156.1 ``--oss``, Claude Code on
Ollama 0.33.3's Anthropic-compatible API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from staff import adapters as adapters_mod
from staff import ollama_env, pricing


@pytest.mark.unit
def test_cursor_agent_runs_unattended_with_stream_json() -> None:
    argv = adapters_mod.ADAPTERS["cursor-agent"].build_command("fix it", "/tmp/wt", model="grok-4.7-high")
    assert argv[:3] == ["cursor-agent", "-p", "fix it"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--force" not in argv and "--trust" in argv  # sandboxed, never forced (#1586)
    assert argv[argv.index("--workspace") + 1] == "/tmp/wt"
    assert argv[-2:] == ["--model", "grok-4.7-high"]


@pytest.mark.unit
def test_cursor_agent_result_event_carries_staff_result_and_camelcase_usage() -> None:
    cursor = adapters_mod.ADAPTERS["cursor-agent"]
    ev = cursor.parse_line(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "OK\nSTAFF_RESULT: ok",
                "usage": {"inputTokens": 9510, "outputTokens": 41, "cacheReadTokens": 3840, "cacheWriteTokens": 0},
            }
        )
    )
    assert "STAFF_RESULT: ok" in ev["text"]
    assert ev["usage"] == {
        "input_tokens": 9510,
        "output_tokens": 41,
        "cache_read_input_tokens": 3840,
        "cache_creation_input_tokens": 0,
    }


@pytest.mark.unit
def test_ollama_provider_is_an_agent_not_bare_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_OLLAMA_URL", "http://10.0.0.1:11434/")
    ollama = adapters_mod.ADAPTERS["ollama"]
    argv = ollama.build_command("fix it", "/tmp/wt", model=None)
    assert argv[:2] == ["codex", "exec"]
    assert "--oss" in argv and argv[argv.index("--local-provider") + 1] == "ollama"
    assert argv[argv.index("--model") + 1] == ollama_env.DEFAULT_OLLAMA_MODEL
    assert not ollama.prompt_via_stdin and argv[-1] == "fix it"
    assert ollama.runtime_env() == {
        "CODEX_OSS_BASE_URL": "http://10.0.0.1:11434/v1",
        "OLLAMA_HOST": "http://10.0.0.1:11434",
    }


@pytest.mark.unit
def test_claude_ollama_uses_its_own_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STAFF_OLLAMA_URL", "http://10.0.0.1:11434")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/seat/claude")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    adapter = adapters_mod.ADAPTERS["claude-ollama"]
    argv = adapter.build_command("fix it", "/tmp/wt", model=None)
    assert argv[0] == "claude" and argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[-2:] == ["--model", ollama_env.DEFAULT_OLLAMA_MODEL]
    env = adapter.runtime_env()
    assert env["ANTHROPIC_BASE_URL"] == "http://10.0.0.1:11434"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "ollama" and env["ANTHROPIC_API_KEY"] == ""
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / ".config" / "runner-dashboard" / "claude-ollama")
    assert Path(env["CLAUDE_CONFIG_DIR"]).is_dir()


@pytest.mark.unit
def test_default_gateway_parses_proc_net_route(tmp_path: Path) -> None:
    table = tmp_path / "route"
    table.write_text(
        "Iface\tDestination\tGateway \tFlags\neth0\t0020A8C0\t00000000\t0001\neth0\t00000000\t012015AC\t0003\n",
        encoding="ascii",
    )
    assert ollama_env.default_gateway(table) == "172.21.32.1"
    assert ollama_env.default_gateway(tmp_path / "missing") is None


@pytest.mark.unit
def test_ollama_url_prefers_localhost_then_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_OLLAMA_URL", raising=False)
    monkeypatch.setattr(ollama_env, "default_gateway", lambda: "172.21.32.1")
    monkeypatch.setattr(ollama_env, "_listening", lambda host, port, timeout=0.3: host == "172.21.32.1")
    assert ollama_env.ollama_base_url() == "http://172.21.32.1:11434"
    monkeypatch.setattr(ollama_env, "_listening", lambda host, port, timeout=0.3: True)
    assert ollama_env.ollama_base_url() == "http://127.0.0.1:11434"
    monkeypatch.setattr(ollama_env, "_listening", lambda host, port, timeout=0.3: False)
    assert ollama_env.ollama_base_url() == "http://127.0.0.1:11434"


@pytest.mark.unit
def test_ollama_backed_providers_are_free() -> None:
    assert {"ollama", "claude-ollama"} <= pricing.FREE_PROVIDERS


@pytest.mark.unit
def test_ollama_backed_runs_lease_as_local_agent() -> None:
    assert adapters_mod.ADAPTERS["ollama"].lease_agent == "local"
    assert adapters_mod.ADAPTERS["claude-ollama"].lease_agent == "local"
    assert adapters_mod.ADAPTERS["cursor-agent"].lease_agent == "cursor-agent"
