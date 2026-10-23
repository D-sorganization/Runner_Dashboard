"""Staff cloud session dispatch (#2627)."""

from __future__ import annotations

import json
import pytest

from staff.cloud_dispatch import (
    API_KEY_ENV_VARS,
    CloudDispatch,
    build_cloud_dispatch,
    cloud_capabilities_payload,
    normalize_provider,
    parse_remote_session_url,
    scrub_api_key_env,
    supports_cloud_execution,
)


def test_scrub_api_key_env_removes_subscription_bypass_keys() -> None:
    env = {
        "PATH": "/usr/bin",
        "ANTHROPIC_API_KEY": "sk-secret",
        "OPENAI_API_KEY": "sk-openai",
        "GEMINI_API_KEY": "g",
        "GOOGLE_API_KEY": "g2",
        "HOME": "/home/x",
    }
    cleaned = scrub_api_key_env(env)
    assert set(API_KEY_ENV_VARS).isdisjoint(cleaned)
    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["HOME"] == "/home/x"


def test_build_claude_remote_argv() -> None:
    spec = build_cloud_dispatch("claude", "fix the flaky test", model="sonnet")
    assert spec.argv[:2] == ["claude", "--remote"]
    assert spec.argv[2] == "fix the flaky test"
    assert spec.is_workflow is False


def test_build_codex_cloud_argv_includes_model_when_set() -> None:
    spec = build_cloud_dispatch("codex", "ship it", model="gpt-5.2")
    assert spec.argv[:3] == ["codex", "cloud", "--json"]
    assert "--model" in spec.argv
    assert spec.argv[-1] == "ship it"


def test_build_cursor_workflow_dispatch() -> None:
    spec = build_cloud_dispatch(
        "cursor",
        "review PR",
        repo="Runner_Dashboard",
        issue=99,
    )
    assert spec.is_workflow is True
    assert spec.argv[0] == "gh"
    assert "Staff-Cloud-Dispatch.yml" in spec.argv[2]
    body = json.loads(spec.stdin or "{}")
    assert body["inputs"]["execution_mode"] == "cloud"
    assert body["inputs"]["provider"] == "cursor-agent"
    assert body["inputs"]["number"] == "99"


def test_build_jules_requires_issue_or_pr() -> None:
    with pytest.raises(ValueError, match="issue or pr"):
        build_cloud_dispatch("jules", "assess", repo="Runner_Dashboard")


def test_unsupported_provider_raises() -> None:
    with pytest.raises(ValueError, match="does not support cloud"):
        build_cloud_dispatch("gemini", "nope")


def test_parse_remote_session_url_claude() -> None:
    line = 'Session ready: https://claude.ai/chat/abc-123  (use claude --teleport)'
    assert parse_remote_session_url("claude", line) == "https://claude.ai/chat/abc-123"


def test_parse_workflow_run_url() -> None:
    text = "Created workflow run: https://github.com/org/repo/actions/runs/555"
    assert parse_remote_session_url("jules", text) == "https://github.com/org/repo/actions/runs/555"


def test_cloud_capabilities_payload() -> None:
    installed = {"claude": True, "codex": False, "gh": True}
    payload = cloud_capabilities_payload(installed)
    assert payload["execution_modes"] == ["local", "cloud"]
    assert payload["providers"]["claude"]["cloud"] is True
    assert payload["providers"]["codex"]["cloud"] is False
    assert payload["providers"]["jules"]["cloud"] is True


def test_normalize_provider_aliases() -> None:
    assert normalize_provider("cursor") == "cursor-agent"
    assert normalize_provider("jules_api") == "jules"


def test_run_body_accepts_execution_mode_cloud() -> None:
    from routers.staff import RunBody

    body = RunBody(prompt="x", provider="claude", execution_mode="cloud")
    assert body.execution_mode == "cloud"

