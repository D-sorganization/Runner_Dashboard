"""#4249 H-14 / B-22: the shepherd pass records why a PR was not armed, and bounds its gh calls."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _part in ("backend", "scripts"):
    if str(_REPO_ROOT / _part) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT / _part))

import automerge_guard as _loaded_guard  # noqa: E402, F401
import merge_shepherd_pass as msp  # noqa: E402


class _Guard:
    """The guard module the pass will import; other test modules reload it by path."""

    def __getattr__(self, name: str) -> Any:
        return getattr(sys.modules["automerge_guard"], name)


automerge_guard = _Guard()


def _outcome(**kwargs: Any) -> Any:
    return automerge_guard.ArmOutcome(**kwargs)


def _arm(
    monkeypatch: pytest.MonkeyPatch, outcome: Any, *, identity_ok: bool = True
) -> tuple[dict[str, Any], dict[str, Any]]:
    seen: dict[str, Any] = {}

    def fake_arm(repo: str, pr: int, **kwargs: Any) -> Any:
        seen.update(kwargs, repo=repo, pr=pr)
        return outcome

    monkeypatch.setattr(sys.modules["automerge_guard"], "check_token_identity", lambda **_k: identity_ok)
    monkeypatch.setattr(sys.modules["automerge_guard"], "arm_with_cap", fake_arm)
    row = msp._arm_via_guard("o/r", 7, strategy="squash", head_sha="abc1234", repo_root=_REPO_ROOT)
    return row, seen


def test_a_refused_arm_carries_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    refused = _outcome(exit_code=1, message="NOT armed o/r#7: `do-not-merge` label", reason="`do-not-merge` label")
    row, _ = _arm(monkeypatch, refused)
    assert row == {"exit_code": 1, "armed": False, "reason": "`do-not-merge` label"}


def test_a_successful_arm_has_no_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    row, seen = _arm(monkeypatch, _outcome(exit_code=0, message="armed o/r#7", armed=True))
    assert row == {"exit_code": 0, "armed": True, "reason": ""}
    assert seen["match_head_commit"] == "abc1234"
    assert seen["strategy"] == "squash"
    assert seen["repo_root"] == str(_REPO_ROOT)


def test_a_non_app_token_is_refused_before_any_arm(monkeypatch: pytest.MonkeyPatch) -> None:
    row, seen = _arm(monkeypatch, _outcome(exit_code=0, message="armed", armed=True), identity_ok=False)
    assert row["exit_code"] == automerge_guard.EXIT_IDENTITY_REFUSED
    assert row["armed"] is False
    assert row["reason"]
    assert seen == {}


def test_the_pass_runner_bounds_its_gh_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(msp.subprocess, "run", fake_run)
    msp._default_runner(["gh", "api", "graphql"])
    assert seen["timeout"] > 0


def test_a_hung_pass_call_becomes_a_failed_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=list(cmd), timeout=kwargs["timeout"])

    monkeypatch.setattr(msp.subprocess, "run", hang)
    proc = msp._default_runner(["gh", "api", "graphql"])
    assert proc.returncode != 0
    assert "timed out" in proc.stderr
