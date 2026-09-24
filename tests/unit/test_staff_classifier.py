"""Unit tests for staff run failure classification (SC-A6, Issue #1297)."""

from __future__ import annotations

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
        "provider_error",
        "rate_limited",
        "needs_input",
        "timeout",
        "stalled",
        "lease_blocked",
        "orphaned",
        "workspace_error",
        "unkillable",
        "unknown",
    }
    assert ALLOWED_FAILURE_CLASSES == expected


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
        machine="OGLaptop",
    )
    assert classification.failure_class == expected_class
    assert classification.retryable is expected_retryable
    assert sign_in_in_remediation.lower() in classification.remediation.lower()


def test_classify_watchdog_timeout() -> None:
    """Watchdog wall-clock timeout is classified as timeout (retryable)."""
    classification = classify_run_failure(
        provider="claude",
        exit_code=-1,
        watchdog_failure_class="timeout",
        watchdog_error="execution wall-clock limit exceeded (4h)",
        machine="DeskComputer",
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
        machine="DeskComputer",
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
        machine="DeskComputer",
    )
    assert classification.failure_class == "unkillable"
    assert classification.retryable is False


def test_classify_orphaned() -> None:
    """Reconciliation marks run orphaned."""
    classification = classify_run_failure(
        provider="claude",
        exit_code=-1,
        watchdog_failure_class="orphaned",
        machine="OGLaptop",
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
        machine="OGLaptop",
    )
    assert classification.failure_class == "needs_input"
    assert classification.retryable is False
    assert classification.question == "Should I proceed with creating the pull request on main?"
    assert "Should I proceed with creating the pull request on main?" in classification.remediation


def test_classify_exit_zero_without_staff_result_not_a_question() -> None:
    """Exit 0 without STAFF_RESULT and without a question is classified as unknown."""
    last_output = "I finished inspecting the code and wrote everything to disk."
    classification = classify_run_failure(
        provider="claude",
        exit_code=0,
        result_line="",
        output_text=last_output,
        machine="OGLaptop",
    )
    assert classification.failure_class == "unknown"
    assert classification.retryable is False


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
            machine="OGLaptop",
            repo="Tools",
            target_kind="issue",
            target_ref="#101",
            prompt="do task",
            status="failed",
            failure_class="auth_expired",
            retryable=False,
            error="401 Unauthorized",
            remediation="Run `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login` on node OGLaptop.",
        ),
        RunRecord(
            id="run-2",
            role="night-watch",
            provider="claude",
            model="sonnet",
            machine="OGLaptop",
            repo="Runner_Dashboard",
            target_kind="issue",
            target_ref="#102",
            prompt="sweep",
            status="failed",
            failure_class="auth_expired",
            retryable=False,
            error="401 Unauthorized",
            remediation="Run `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login` on node OGLaptop.",
        ),
        RunRecord(
            id="run-3",
            role="worker",
            provider="codex",
            model=None,
            machine="OGLaptop",
            repo="Tools",
            target_kind="issue",
            target_ref="#103",
            prompt="compile",
            status="failed",
            failure_class="auth_expired",
            retryable=False,
            error="Codex auth expired",
            remediation="Run `codex login --device-auth` on node OGLaptop.",
        ),
        RunRecord(
            id="run-4",
            role="tester",
            provider="claude",
            model="sonnet",
            machine="OGLaptop",
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
    # Expected: 1 for claude auth on OGLaptop, 1 for codex auth on OGLaptop, 1 for timeout on run-4
    assert len(items) == 3

    auth_claude = next(
        (i for i in items if i.get("provider") == "claude" and i.get("failure_class") == "auth_expired"),
        None,
    )
    assert auth_claude is not None
    assert auth_claude["machine"] == "OGLaptop"
    assert "CLAUDE_CONFIG_DIR" in auth_claude["remediation"]
    assert "run-1" in auth_claude.get("affected_runs", [])
    assert "run-2" in auth_claude.get("affected_runs", [])

    timeout_item = next((i for i in items if i.get("id") == "run-4"), None)
    assert timeout_item is not None
    assert timeout_item["failure_class"] == "timeout"
    assert timeout_item["retryable"] is True
