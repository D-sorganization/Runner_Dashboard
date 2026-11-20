"""GitHub coordination evidence failures never make real CLI or governor calls."""

from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest.mock import Mock

import pytest
from coordination import gh_cli
from github_rate_governor import GitHubGovernorExhausted

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh_cli, "_rm_env", lambda: {"PATH": "/isolated/fleet-bin"})
    monkeypatch.setattr(gh_cli, "acquire_before_github_call", Mock())
    monkeypatch.setattr(gh_cli, "record_github_rate_limit", Mock())
    monkeypatch.setattr(gh_cli.subprocess, "run", Mock(side_effect=AssertionError("unconfigured CLI call")))


@pytest.mark.parametrize("value", [{"state": "open"}, [{"id": 1}], None, 42, "text"])
def test_lookup_returns_decoded_json(monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    invoke = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(value), ""))
    monkeypatch.setattr(gh_cli.subprocess, "run", invoke)
    assert gh_cli.gh_api_json("/repos/example/repo/issues/1") == value
    invoke.assert_called_once_with(
        ["gh", "api", "/repos/example/repo/issues/1"],
        capture_output=True,
        text=True,
        timeout=gh_cli.GH_TIMEOUT_SECONDS,
        check=False,
        env={"PATH": "/isolated/fleet-bin"},
    )
    gh_cli.acquire_before_github_call.assert_called_once_with()
    gh_cli.record_github_rate_limit.assert_not_called()


@pytest.mark.parametrize("output", ["", " \n\t"])
def test_empty_successful_response_is_unavailable(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    monkeypatch.setattr(gh_cli.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, output, "")))
    with pytest.raises(RuntimeError, match="returned empty stdout"):
        gh_cli.gh_api_json("/example")


def test_malformed_response_preserves_json_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh_cli.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, "not JSON", "")))
    with pytest.raises(json.JSONDecodeError):
        gh_cli.gh_api_json("/example")


@pytest.mark.parametrize(
    ("stderr", "stdout", "expected"),
    [
        (" stderr ", "stdout", "stderr"),
        ("", " stdout ", "stdout"),
        ("", "", "gh api /example failed"),
        ("x" * 400, "", "x" * 300),
    ],
)
def test_command_errors_have_bounded_diagnostic_precedence(
    monkeypatch: pytest.MonkeyPatch, stderr: str, stdout: str, expected: str
) -> None:
    monkeypatch.setattr(gh_cli.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 1, stdout, stderr)))
    monkeypatch.setattr(gh_cli, "looks_rate_limited_output", lambda text: False)
    with pytest.raises(RuntimeError) as caught:
        gh_cli.gh_api_json("/example")
    assert str(caught.value) == expected
    gh_cli.record_github_rate_limit.assert_not_called()


@pytest.mark.parametrize("operation", ["lookup", "remove"])
def test_governor_denial_prevents_subprocess(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    error = GitHubGovernorExhausted(retry_after_seconds=12.8, identity="fixture")
    monkeypatch.setattr(gh_cli, "acquire_before_github_call", Mock(side_effect=error))
    with pytest.raises(RuntimeError, match="retry after 12s") as caught:
        if operation == "lookup":
            gh_cli.gh_api_json("/example")
        else:
            gh_cli.remove_issue_label("example/repo", 1, "fixture")
    assert caught.value.__cause__ is error
    gh_cli.subprocess.run.assert_not_called()


@pytest.mark.parametrize("operation", ["lookup", "remove"])
def test_rate_limit_records_retry_after_from_both_streams(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    monkeypatch.setattr(
        gh_cli.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 1, "stdout", "stderr"))
    )
    detector = Mock(return_value=True)
    parser = Mock(return_value=73.0)
    monkeypatch.setattr(gh_cli, "looks_rate_limited_output", detector)
    monkeypatch.setattr(gh_cli, "parse_gh_cli_retry_after_seconds", parser)
    with pytest.raises(RuntimeError):
        if operation == "lookup":
            gh_cli.gh_api_json("/example")
        else:
            gh_cli.remove_issue_label("example/repo", 1, "fixture")
    detector.assert_called_once_with("stderr\nstdout")
    parser.assert_called_once_with("stderr\nstdout")
    gh_cli.record_github_rate_limit.assert_called_once_with(retry_after_seconds=73.0)


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired(["gh"], 20), FileNotFoundError("fixture executable absent")]
)
def test_transport_failures_are_not_converted_to_empty_evidence(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(gh_cli.subprocess, "run", Mock(side_effect=error))
    with pytest.raises(type(error)) as caught:
        gh_cli.gh_api_json("/example")
    assert caught.value is error
    gh_cli.record_github_rate_limit.assert_not_called()


def test_paginated_lookup_delegates_with_sanitized_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = [{"id": 1}, {"id": 2}]
    paginate = Mock(return_value=expected)
    monkeypatch.setattr(gh_cli, "gh_cli_paginate", paginate)
    assert gh_cli.gh_api_json_all("/example?state=open") is expected
    paginate.assert_called_once_with(
        "/example?state=open", env={"PATH": "/isolated/fleet-bin"}, timeout=gh_cli.GH_TIMEOUT_SECONDS
    )
    gh_cli.subprocess.run.assert_not_called()
