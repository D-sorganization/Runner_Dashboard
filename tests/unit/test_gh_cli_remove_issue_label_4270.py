"""#4270 B-08: ``gh_cli.remove_issue_label`` deletes one label and treats "already absent" as done."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from coordination import gh_cli


def _fake_run(monkeypatch: pytest.MonkeyPatch, returncode: int, stderr: str = "") -> list[list[str]]:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, "", stderr)

    monkeypatch.setattr(gh_cli.subprocess, "run", run)
    monkeypatch.setattr(gh_cli, "acquire_before_github_call", lambda: None)
    return calls


@pytest.mark.unit
def test_deletes_the_label_with_an_escaped_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_run(monkeypatch, 0)

    gh_cli.remove_issue_label("D-sorganization/Tools", 4301, "claim:codex")

    assert calls == [["gh", "api", "-X", "DELETE", "/repos/D-sorganization/Tools/issues/4301/labels/claim%3Acodex"]]


@pytest.mark.unit
def test_label_already_absent_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, 1, "gh: Label does not exist (HTTP 404)")

    gh_cli.remove_issue_label("D-sorganization/Tools", 4301, "claim:codex")


@pytest.mark.unit
@pytest.mark.parametrize("stderr", ["gh: Not Found (HTTP 404)", "gh: Server Error (HTTP 500)", ""])
def test_other_failures_raise(monkeypatch: pytest.MonkeyPatch, stderr: str) -> None:
    """A 404 for the issue or repository is not "label absent"."""
    _fake_run(monkeypatch, 1, stderr)

    with pytest.raises(RuntimeError):
        gh_cli.remove_issue_label("D-sorganization/Tools", 4301, "claim:codex")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("repo", "issue", "label"),
    [("Tools", 1, "claim:codex"), ("D-sorganization/Tools", 0, "claim:codex"), ("D-sorganization/Tools", 1, " ")],
)
def test_rejects_malformed_arguments_before_calling_gh(
    monkeypatch: pytest.MonkeyPatch, repo: str, issue: int, label: str
) -> None:
    calls = _fake_run(monkeypatch, 0)

    with pytest.raises(ValueError, match="remove_issue_label"):
        gh_cli.remove_issue_label(repo, issue, label)

    assert calls == []
