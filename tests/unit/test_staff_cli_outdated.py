"""Unit tests for cli_outdated failure classification (Issue #1669).

A provider CLI that rejects an adapter flag (a too-old install) is a distinct,
actionable failure: `failure_class: cli_outdated`, not retryable, with a
remediation naming the provider, the node, the rejected option and the
upgrade command.
"""

from __future__ import annotations

import pytest
from staff.chat_failures import failure_specificity
from staff.classifier import ALLOWED_FAILURE_CLASSES, classify_run_failure
from staff.retry import NON_RETRYABLE_FAILURE_CLASSES, is_retryable_class


def test_cli_outdated_in_allowed_failure_classes() -> None:
    """cli_outdated must be a recognized failure class."""
    assert "cli_outdated" in ALLOWED_FAILURE_CLASSES


@pytest.mark.unit
def test_unknown_option_classifies_as_cli_outdated() -> None:
    """A rejected `--permission-prompts` flag classifies as cli_outdated (#1669)."""
    classification = classify_run_failure(
        "claude",
        1,
        error_message="error: unknown option '--permission-prompts'",
    )
    assert classification.failure_class == "cli_outdated"
    assert classification.retryable is False


@pytest.mark.unit
def test_cli_outdated_remediation_names_option_node_and_upgrade_command() -> None:
    """The remediation names the rejected option, the node, and the upgrade command."""
    classification = classify_run_failure(
        "claude",
        1,
        error_message="error: unknown option '--permission-prompts'",
        machine="DeskComputer",
    )
    assert "--permission-prompts" in classification.remediation
    assert "DeskComputer" in classification.remediation
    assert "npm install -g @anthropic-ai/claude-code@latest" in classification.remediation


@pytest.mark.unit
@pytest.mark.parametrize(
    "error_message",
    [
        "error: unknown option '--permission-prompts'",
        "error: unknown argument '--permission-prompts'",
        "error: unrecognized arguments: --permission-prompts",
        "error: unexpected argument '--permission-prompts' found",
    ],
)
def test_cli_option_rejection_patterns_classify_as_cli_outdated(error_message: str) -> None:
    """Every known CLI-option-rejection phrasing classifies as cli_outdated."""
    classification = classify_run_failure("claude", 1, error_message=error_message)
    assert classification.failure_class == "cli_outdated"
    assert classification.retryable is False


@pytest.mark.unit
def test_auth_error_still_classifies_as_auth_expired() -> None:
    """A 401 auth failure is unaffected by the new cli_outdated branch."""
    classification = classify_run_failure(
        "claude",
        1,
        error_message="401 unauthorized",
    )
    assert classification.failure_class == "auth_expired"


@pytest.mark.unit
def test_cli_outdated_is_non_retryable() -> None:
    """retry.py treats cli_outdated as non-retryable."""
    assert "cli_outdated" in NON_RETRYABLE_FAILURE_CLASSES
    assert is_retryable_class("cli_outdated") is False


@pytest.mark.unit
def test_cli_outdated_chat_specificity_beats_unknown() -> None:
    """cli_outdated ranks more specific than the generic unknown class."""
    assert failure_specificity("cli_outdated") > failure_specificity("unknown")


@pytest.mark.unit
def test_transcript_mentioning_unknown_option_is_not_cli_outdated() -> None:
    """Agent prose that merely mentions the phrase is not the CLI's own parser error."""
    output = 'I fixed the handler that printed "unknown option" for bad flags.\nDone, tests fail though.'
    classification = classify_run_failure("claude", 1, output_text=output)
    assert classification.failure_class != "cli_outdated"
