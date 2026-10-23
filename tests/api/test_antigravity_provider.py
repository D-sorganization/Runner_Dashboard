"""TDD contract tests for agy (Antigravity) CLI dispatch provider (#2021).

Acceptance criteria covered:
1. First-class provider in provider registry and executor router.
2. POST /api/staff/{role}/run launches agy CLI headless and non-interactive.
3. Model passthrough: RunBody.model maps to agy CLI's --model flag; unknown model fails fast.
4. Output contract: STAFF_RESULT: parsed, token/cost accounting extracted, quota failure classified.
5. Provider probe: distinguishes installed, authenticated, and quota-available states.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from agent_remediation.provider_probe import probe_provider_availability
from agent_remediation.provider_registry import (
    _ANTIGRAVITY_MODELS,
    PROVIDER_REGISTRY,
)
from code_requests.executor_models import ChildExecutionRecord, ExecutorTier
from code_requests.executor_router import (
    DEFAULT_PROVIDERS_BY_TIER,
    resolve_child_executor,
)
from routers.credentials import _probe_antigravity_cli
from staff import adapters as adapters_mod
from staff.classifier import classify_run_failure
from staff.pricing import estimate_cost

# ── 1. Provider Registry Contract ─────────────────────────────────────────────


@pytest.mark.unit
def test_provider_registry_contains_antigravity_with_models() -> None:
    entry = next((e for e in PROVIDER_REGISTRY if e.dashboard_id == "antigravity"), None)
    assert entry is not None, "antigravity must be in PROVIDER_REGISTRY"
    assert entry.conductor_id == "antigravity-cli"
    assert entry.availability_probe == ("agy",)
    assert entry.credential_id == "antigravity"
    assert entry.auth_mode == "local"
    assert entry.models == _ANTIGRAVITY_MODELS
    assert len(entry.models) >= 2
    assert "gemini-3.8-flash-high" in entry.models
    assert "gemini-4-argon-high" in entry.models


@pytest.mark.unit
def test_executor_router_includes_antigravity_in_cli_and_strong_tiers() -> None:
    assert "antigravity" in DEFAULT_PROVIDERS_BY_TIER[ExecutorTier.CLI]
    assert "antigravity" in DEFAULT_PROVIDERS_BY_TIER[ExecutorTier.STRONG]


@pytest.mark.unit
def test_resolve_child_executor_with_pinned_antigravity() -> None:
    child = ChildExecutionRecord(
        key="test-1",
        title="Test child",
        tier=ExecutorTier.CLI,
        task_class="ci_fix",
        repository="test-owner/test-repo",
    )
    req = MagicMock()
    req.executor_profile_id = "antigravity"
    provider, tier, reason = resolve_child_executor(child, request=req)
    assert provider == "antigravity"
    assert tier == ExecutorTier.STRONG
    assert "pinned_profile" in reason


# ── 2. Staff Adapter CLI Contract & Model Validation ──────────────────────────


@pytest.mark.unit
def test_antigravity_adapter_build_command_with_valid_model() -> None:
    adapter = adapters_mod.ADAPTERS["antigravity"]
    argv = adapter.build_command("run tests", "/tmp/repo", model="gemini-3.8-flash-high")
    assert argv[0] == "agy"
    assert "--print" in argv
    assert argv[argv.index("--print") + 1] == "run tests"
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--add-dir" in argv
    assert argv[argv.index("--add-dir") + 1] == "/tmp/repo"
    assert "--mode" in argv
    assert argv[argv.index("--mode") + 1] == "auto"
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-high"


@pytest.mark.unit
def test_antigravity_session_uses_conversation_flag_for_run_and_chat() -> None:
    adapter = adapters_mod.ADAPTERS["antigravity"]
    run = adapter.build_command("continue", "/tmp/repo", session_id="session-1")
    chat = adapter.chat_argv("continue", "/tmp/repo", session_id="session-1")
    assert run[run.index("--conversation") + 1] == "session-1"
    assert chat[chat.index("--conversation") + 1] == "session-1"
    assert "--resume" not in run and "--resume" not in chat


@pytest.mark.unit
def test_antigravity_adapter_build_command_default_model() -> None:
    adapter = adapters_mod.ADAPTERS["antigravity"]
    argv = adapter.build_command("run tests", "/tmp/repo", model=None)
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-high"


@pytest.mark.unit
def test_antigravity_adapter_build_command_unknown_model_fails_fast() -> None:
    adapter = adapters_mod.ADAPTERS["antigravity"]
    with pytest.raises(
        ValueError,
        match="unknown model 'non-existent-model-xyz' for provider 'antigravity'",
    ):
        adapter.build_command("run tests", "/tmp/repo", model="non-existent-model-xyz")


# ── 3. Output Contract & Result Parsing ───────────────────────────────────────


@pytest.mark.unit
def test_antigravity_parse_line_extracts_result_text_and_usage() -> None:
    adapter = adapters_mod.ADAPTERS["antigravity"]
    raw_event = {
        "event": "result",
        "result": {
            "status": "SUCCESS",
            "response": "Summary of changes made.\n\nSTAFF_RESULT: ok\n",
            "usage": {
                "input_tokens": 12500,
                "output_tokens": 820,
                "total_tokens": 13320,
            },
        },
    }
    parsed = adapter.parse_line(json.dumps(raw_event))
    assert parsed["kind"] == "result"
    assert "STAFF_RESULT: ok" in parsed["text"]
    assert parsed["usage"]["input_tokens"] == 12500
    assert parsed["usage"]["output_tokens"] == 820
    assert parsed["usage"]["total_tokens"] == 13320


@pytest.mark.unit
def test_antigravity_pricing_token_cost_estimation() -> None:
    usd, method = estimate_cost(
        "antigravity",
        "gemini-3.8-flash-high",
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    assert usd > 0.0
    assert method == "token_table"


# ── 4. Failure & Quota Classification ─────────────────────────────────────────


@pytest.mark.unit
def test_antigravity_quota_exhaustion_classified_as_rate_limited_or_capacity() -> None:
    # Google/Gemini quota errors typically mention RESOURCE_EXHAUSTED or quota exceeded
    err_msg = "Error: RESOURCE_EXHAUSTED: quota exceeded for quota metric 'Queries' and limit 'Queries per min'"
    failure = classify_run_failure(
        provider="antigravity",
        output_text=err_msg,
        exit_code=1,
    )
    assert failure.retryable is True
    assert failure.failure_class in ("rate_limited", "provider_capacity", "quota_exhausted")

    failure_capacity = classify_run_failure(
        provider="antigravity",
        output_text="Error: selected model is at capacity",
        exit_code=1,
    )
    assert failure_capacity.retryable is True
    assert failure_capacity.failure_class == "provider_capacity"


# ── 5. Provider Probe Contract ────────────────────────────────────────────────


@pytest.mark.unit
def test_probe_antigravity_cli_installed_and_authenticated() -> None:
    with (
        patch("routers.credentials.shutil.which", return_value="/usr/local/bin/agy"),
        patch("routers.credentials._find_binary", return_value="/usr/local/bin/agy"),
        patch("routers.credentials._antigravity_login_present", return_value=True),
        patch(
            "staff.cli_oauth_session.antigravity_oauth_session_alive",
            return_value=(True, "OAuth session active"),
        ),
        patch("staff.quota.headroom", return_value=(True, "ok")),
    ):
        probe = _probe_antigravity_cli()
        assert probe["id"] == "antigravity"
        assert probe["installed"] is True
        assert probe["authenticated"] is True
        assert probe["quota_available"] is True
        assert probe["status"] == "ready"


@pytest.mark.unit
def test_probe_antigravity_cli_not_installed() -> None:
    with (
        patch("routers.credentials.shutil.which", return_value=None),
        patch("routers.credentials._find_binary", return_value=None),
        patch("routers.credentials._cli_login_file_present", return_value=False),
        patch("routers.credentials._env_present", return_value=False),
        patch("staff.quota.headroom", return_value=(True, "ok")),
    ):
        probe = _probe_antigravity_cli()
        assert probe["installed"] is False
        assert probe["authenticated"] is False
        assert probe["status"] == "not_installed"


@pytest.mark.unit
def test_probe_antigravity_cli_installed_not_authenticated() -> None:
    with (
        patch("routers.credentials.shutil.which", return_value="/usr/local/bin/agy"),
        patch("routers.credentials._find_binary", return_value="/usr/local/bin/agy"),
        patch("routers.credentials._cli_login_file_present", return_value=False),
        patch("routers.credentials._env_present", return_value=False),
        patch("staff.quota.headroom", return_value=(True, "ok")),
    ):
        probe = _probe_antigravity_cli()
        assert probe["installed"] is True
        assert probe["authenticated"] is False
        assert probe["status"] == "missing_key"


@pytest.mark.unit
def test_probe_antigravity_cli_quota_exhausted() -> None:
    with (
        patch("routers.credentials.shutil.which", return_value="/usr/local/bin/agy"),
        patch("routers.credentials._find_binary", return_value="/usr/local/bin/agy"),
        patch("routers.credentials._antigravity_login_present", return_value=True),
        patch(
            "staff.cli_oauth_session.antigravity_oauth_session_alive",
            return_value=(True, "OAuth session active"),
        ),
        patch(
            "staff.quota.headroom",
            return_value=(False, "antigravity: plan limit reached"),
        ),
    ):
        probe = _probe_antigravity_cli()
        assert probe["installed"] is True
        assert probe["authenticated"] is True
        assert probe["quota_available"] is False
        assert probe["status"] == "quota_exhausted"


@pytest.mark.unit
def test_provider_probe_availability_includes_antigravity() -> None:
    with (
        patch("routers.credentials.shutil.which", return_value="/usr/local/bin/agy"),
        patch(
            "agent_remediation.provider_probe.shutil.which",
            return_value="/usr/local/bin/agy",
        ),
        patch("routers.credentials._find_binary", return_value="/usr/local/bin/agy"),
        patch("routers.credentials._antigravity_login_present", return_value=True),
        patch(
            "staff.cli_oauth_session.antigravity_oauth_session_alive",
            return_value=(True, "OAuth session active"),
        ),
        patch("staff.quota.headroom", return_value=(True, "ok")),
    ):
        res = probe_provider_availability()
        assert "antigravity" in res
        assert res["antigravity"]["installed"] is True
        assert res["antigravity"]["authenticated"] is True
        assert set(res["antigravity"].keys()) == {"installed", "authenticated", "detail"}
