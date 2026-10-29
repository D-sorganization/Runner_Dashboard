"""Unit tests for staff run failure classification (SC-A6, Issue #1297)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.classifier import (
    ALLOWED_FAILURE_CLASSES,
    classify_run_failure,
    format_attention_items,
)
from staff.store import RunRecord


def test_allowed_failure_classes_complete() -> None:
    """Allowed failure classes must match the specification."""
    expected = {
        "auth_expired",
        "cli_missing",
        "cli_outdated",
        "config",
        "provider_capacity",
        "provider_error",
        "providers_exhausted",
        "quota_exhausted",
        "rate_limited",
        "needs_input",
        "timeout",
        "stalled",
        "lease_blocked",
        "orphaned",
        "workspace_error",
        "environment_error",
        "unkillable",
        "unknown",
        "no_result",
    }
    assert ALLOWED_FAILURE_CLASSES == expected


CODEX_BWRAP_TRANSCRIPT = (
    "shell execution fails before running any command: the sandbox reports "
    "`bwrap: No permissions to create new namespace`\n"
    "STAFF_RESULT: ok\n"
)


def test_codex_bwrap_namespace_failure_is_environment_error() -> None:
    diag = classify_run_failure("codex", 0, output_text=CODEX_BWRAP_TRANSCRIPT)
    assert diag.failure_class == "environment_error"
    assert diag.retryable is False
    assert "unprivileged_userns_clone" in diag.remediation
    assert "config.toml" in diag.remediation


def test_classify_execution_result_fails_success_with_bwrap_in_transcript(tmp_path) -> None:
    from staff.execution_result import classify_execution_result

    transcript = tmp_path / "t.log"
    transcript.write_text(CODEX_BWRAP_TRANSCRIPT, encoding="utf-8")
    status, failure_class, retryable, remediation, error = classify_execution_result(
        cancelled=False,
        rc=0,
        result_line="STAFF_RESULT: ok",
        provider="codex",
        transcript_path=transcript,
        machine="hub-a",
    )
    assert status == "failed"
    assert failure_class == "environment_error"
    assert not retryable
    assert "wsl.conf" in remediation.lower()
    assert error


def test_exit0_run_with_bwrap_error_recorded_failed(tmp_path: Path) -> None:
    """#2771: a bwrap/namespace failure in the transcript fails the run even with no STAFF_RESULT line."""
    from staff.execution_result import classify_execution_result

    transcript = tmp_path / "t.log"
    transcript.write_text(CODEX_BWRAP_TRANSCRIPT, encoding="utf-8")
    status, failure_class, _retryable, _remediation, error = classify_execution_result(
        cancelled=False,
        rc=0,
        result_line="",
        provider="codex",
        transcript_path=transcript,
        machine="DeskComputer",
    )
    assert status == "failed"
    assert failure_class == "environment_error"
    assert error


def test_staff_result_line_does_not_mask_sandbox_failure(tmp_path: Path) -> None:
    """#2771: a trailing STAFF_RESULT line does not mask a sandbox failure earlier in the transcript."""
    from staff.execution_result import classify_execution_result

    transcript = tmp_path / "t.log"
    transcript.write_text(CODEX_BWRAP_TRANSCRIPT, encoding="utf-8")
    status, failure_class, _retryable, _remediation, _error = classify_execution_result(
        cancelled=False,
        rc=0,
        result_line="STAFF_RESULT: ok",
        provider="codex",
        transcript_path=transcript,
        machine="DeskComputer",
    )
    assert status == "failed"
    assert failure_class == "environment_error"


def test_clean_run_still_succeeds(tmp_path: Path) -> None:
    """#2771 regression: a clean exit-0 run with no sandbox pattern in the transcript still succeeds."""
    from staff.execution_result import classify_execution_result

    transcript = tmp_path / "t.log"
    transcript.write_text("All good.\nSTAFF_RESULT: ok\n", encoding="utf-8")
    status, failure_class, retryable, remediation, error = classify_execution_result(
        cancelled=False,
        rc=0,
        result_line="STAFF_RESULT: ok",
        provider="codex",
        transcript_path=transcript,
        machine="DeskComputer",
    )
    assert status == "succeeded"
    assert failure_class == ""
    assert not retryable
    assert remediation == ""
    assert error == ""


@pytest.mark.parametrize(
    ("provider", "rc", "text", "expected_class", "expected_retryable", "sign_in_in_remediation"),
    [
        (
            "claude",
            1,
            "Error: 401 OAuth access token has expired. Please run claude auth login",
            "auth_expired",
            False,
            "claude auth login",
        ),
        (
            "codex",
            1,
            "Missing organization in id_token claims. Please login with codex login --device-auth",
            "auth_expired",
            False,
            "codex login --device-auth",
        ),
        (
            "cursor-agent",
            1,
            "Authentication failed: token expired. Run cursor-agent login",
            "auth_expired",
            False,
            "cursor-agent login",
        ),
        (
            "claude",
            127,
            "/bin/bash: claude: command not found",
            "cli_missing",
            False,
            "Install `claude`",
        ),
        (
            "codex",
            1,
            "FileNotFoundError: [Errno 2] No such file or directory: 'codex'",
            "cli_missing",
            False,
            "Install `codex`",
        ),
        (
            "claude",
            1,
            "API rate limit exceeded (HTTP 429): please retry after 60 seconds",
            "rate_limited",
            True,
            "rate limit",
        ),
        (
            "codex",
            1,
            "Error code: 429 - {'error': {'message': 'Rate limit reached', 'type': 'rate_limit_exceeded'}}",
            "rate_limited",
            True,
            "rate limit",
        ),
        (
            "ollama",
            1,
            "Failed to connect to Ollama at http://127.0.0.1:11434: Connection refused",
            "provider_error",
            True,
            "Ollama",
        ),
        (
            "claude",
            1,
            "HTTP 502 Bad Gateway from Anthropic API",
            "provider_error",
            True,
            "502",
        ),
        (
            "codex",
            1,
            "Fatal: lease already held on issue #100 by agent orchestrator (409 Conflict)",
            "lease_blocked",
            True,
            "lease",
        ),
        (
            "claude",
            1,
            "fatal: 'staff/feat-100' is already checked out at '/worktrees/dirty'",
            "workspace_error",
            False,
            "Workspace",
        ),
    ],
)
def test_table_driven_classifier_patterns(
    provider: str,
    rc: int,
    text: str,
    expected_class: str,
    expected_retryable: bool,
    sign_in_in_remediation: str,
) -> None:
    """Table-driven verification of failure patterns across providers."""
    classification = classify_run_failure(
        provider=provider,
        exit_code=rc,
        output_text=text,
        machine="node-b",
    )
    assert classification.failure_class == expected_class
    assert classification.retryable is expected_retryable
    assert sign_in_in_remediation.lower() in classification.remediation.lower()


def test_classify_codex_unsupported_chatgpt_model_as_config() -> None:
    """Unsupported Codex ChatGPT-account models are config failures, not unknown (#2022)."""
    text = "Error code: 400 - The 'gpt-5.5' model is not supported when using Codex with a ChatGPT account"
    classification = classify_run_failure(
        provider="codex",
        exit_code=1,
        output_text=text,
        machine="hub-a",
    )
    assert classification.failure_class == "config"
    assert classification.retryable is False
    assert (
        "gpt-6-luna" in classification.remediation.lower()
        or "config/staff_provider_defaults" in classification.remediation
    )


def test_classify_watchdog_timeout() -> None:
    """Watchdog wall-clock timeout is classified as timeout (retryable)."""
    classification = classify_run_failure(
        provider="claude",
        exit_code=-1,
        watchdog_failure_class="timeout",
        watchdog_error="execution wall-clock limit exceeded (4h)",
        machine="hub-a",
    )
    assert classification.failure_class == "timeout"
    assert classification.retryable is True
    assert "timeout" in classification.remediation.lower()


def test_classify_watchdog_stalled() -> None:
    """Watchdog idle timeout is classified as stalled (retryable)."""
    classification = classify_run_failure(
        provider="codex",
        exit_code=-1,
        watchdog_failure_class="stalled",
        watchdog_error="idle timeout (20m without output)",
        machine="hub-a",
    )
    assert classification.failure_class == "stalled"
    assert classification.retryable is True
    assert "idle" in classification.remediation.lower() or "stalled" in classification.remediation.lower()


def test_classify_watchdog_unkillable() -> None:
    """Watchdog unkillable process is classified as unkillable (not retryable)."""
    classification = classify_run_failure(
        provider="codex",
        exit_code=-1,
        watchdog_failure_class="unkillable",
        watchdog_error="process PID 9999 failed to terminate",
        machine="hub-a",
    )
    assert classification.failure_class == "unkillable"
    assert classification.retryable is False


def test_classify_orphaned() -> None:
    """Reconciliation marks run orphaned."""
    classification = classify_run_failure(
        provider="claude",
        exit_code=-1,
        watchdog_failure_class="orphaned",
        machine="node-b",
    )
    assert classification.failure_class == "orphaned"
    assert classification.retryable is False


def test_classify_needs_input_when_exit_zero_ends_in_question() -> None:
    """An agent that exits 0 without STAFF_RESULT and ends in a question is classified as needs_input."""
    last_output = "I have analyzed the repository.\nShould I proceed with creating the pull request on main?"
    classification = classify_run_failure(
        provider="claude",
        exit_code=0,
        result_line="",  # No STAFF_RESULT
        output_text=last_output,
        machine="node-b",
    )
    assert classification.failure_class == "needs_input"
    assert classification.retryable is False
    assert classification.question == "Should I proceed with creating the pull request on main?"
    assert "Should I proceed with creating the pull request on main?" in classification.remediation
    assert classification.error == "agent paused asking: Should I proceed with creating the pull request on main?"


def test_classify_exit_zero_without_staff_result_not_a_question() -> None:
    """Exit 0 without STAFF_RESULT and without a question is classified as no_result."""
    last_output = "I finished inspecting the code and wrote everything to disk."
    classification = classify_run_failure(
        provider="claude",
        exit_code=0,
        result_line="",
        output_text=last_output,
        machine="node-b",
    )
    assert classification.failure_class == "no_result"
    assert classification.retryable is False
    assert classification.error == "agent exited 0 without a STAFF_RESULT line"


def test_classify_unknown_pattern_attaches_last_lines() -> None:
    """Unknown failures attach the last lines of output to remediation without crashing."""
    lines = [f"Line {i} of mysterious failure" for i in range(30)]
    classification = classify_run_failure(
        provider="custom-agent",
        exit_code=42,
        output_text="\n".join(lines),
        machine="Worker-3",
    )
    assert classification.failure_class == "unknown"
    assert classification.retryable is False
    assert "Line 29 of mysterious failure" in classification.remediation
    assert "Line 0 of mysterious failure" not in classification.remediation  # only last 20 lines


def test_format_attention_items_deduplicates_auth_expired() -> None:
    """Multiple auth_expired runs on the same node and provider yield exactly one attention item."""
    runs = [
        RunRecord(
            id="run-1",
            role="coder",
            provider="claude",
            model="sonnet",
            machine="node-b",
            repo="Tools",
            target_kind="issue",
            target_ref="#101",
            prompt="do task",
            status="failed",
            failure_class="auth_expired",
            retryable=False,
            error="401 Unauthorized",
            remediation="Run `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login` on node node-b.",
        ),
        RunRecord(
            id="run-2",
            role="night-watch",
            provider="claude",
            model="sonnet",
            machine="node-b",
            repo="Runner_Dashboard",
            target_kind="issue",
            target_ref="#102",
            prompt="sweep",
            status="failed",
            failure_class="auth_expired",
            retryable=False,
            error="401 Unauthorized",
            remediation="Run `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login` on node node-b.",
        ),
        RunRecord(
            id="run-3",
            role="worker",
            provider="codex",
            model=None,
            machine="node-b",
            repo="Tools",
            target_kind="issue",
            target_ref="#103",
            prompt="compile",
            status="failed",
            failure_class="auth_expired",
            retryable=False,
            error="Codex auth expired",
            remediation="Run `codex login --device-auth` on node node-b.",
        ),
        RunRecord(
            id="run-4",
            role="tester",
            provider="claude",
            model="sonnet",
            machine="node-b",
            repo="Tools",
            target_kind="issue",
            target_ref="#104",
            prompt="run tests",
            status="failed",
            failure_class="timeout",
            retryable=True,
            error="timed out",
            remediation="Execution deadline exceeded.",
        ),
    ]

    items = format_attention_items(runs)
    # Expected: 1 for claude auth on node-b, 1 for codex auth on node-b, 1 for timeout on run-4
    assert len(items) == 3

    auth_claude = next(
        (i for i in items if i.get("provider") == "claude" and i.get("failure_class") == "auth_expired"),
        None,
    )
    assert auth_claude is not None
    assert auth_claude["machine"] == "node-b"
    assert "CLAUDE_CONFIG_DIR" in auth_claude["remediation"]
    assert "run-1" in auth_claude.get("affected_runs", [])
    assert "run-2" in auth_claude.get("affected_runs", [])

    timeout_item = next((i for i in items if i.get("id") == "run-4"), None)
    assert timeout_item is not None
    assert timeout_item["failure_class"] == "timeout"
    assert timeout_item["retryable"] is True
