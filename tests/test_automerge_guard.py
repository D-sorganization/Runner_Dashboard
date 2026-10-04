"""Hermetic tests for scripts/automerge_guard.py — no network, no real gh."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

# scripts/ is not an importable package, so load the module by path. It must be
# registered in sys.modules before exec_module or @dataclass cannot resolve its
# own module namespace.
_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "automerge_guard.py"
_SPEC = importlib.util.spec_from_file_location("automerge_guard", _MODULE_PATH)
assert _SPEC and _SPEC.loader
automerge_guard = importlib.util.module_from_spec(_SPEC)
sys.modules["automerge_guard"] = automerge_guard
_SPEC.loader.exec_module(automerge_guard)

HEAD_SHA = "abc123"
HEAD_DATE = "2026-08-13T20:35:02Z"


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeGh:
    """Records gh invocations and replays canned responses."""

    def __init__(
        self,
        *,
        draft: bool = False,
        labels: Sequence[str] = (),
        body: str = "",
        disarms: Sequence[str] = (),
        removed: Sequence[str] = (),
        head_date: str = HEAD_DATE,
        merge_returncode: int = 0,
    ) -> None:
        self.pull = {
            "draft": draft,
            "labels": [{"name": name} for name in labels],
            "body": body,
            "head": {"sha": HEAD_SHA},
        }
        self.disarms = list(disarms)
        self.removed = list(removed)
        self.head_date = head_date
        self.merge_returncode = merge_returncode
        self.calls: list[list[str]] = []

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(cmd)
        self.calls.append(argv)
        joined = " ".join(argv)
        if "pr" in argv and "merge" in argv:
            return _completed(
                returncode=self.merge_returncode,
                stderr="merge boom" if self.merge_returncode else "",
            )
        if "/timeline" in joined:
            return _completed("\n".join(self.disarms))
        if "/commits/" in joined:
            return _completed(self.head_date)
        if "/files" in joined:
            return _completed("\n".join(self.removed))
        if "/pulls/" in joined:
            return _completed(json.dumps(self.pull))
        raise AssertionError(f"unexpected gh call: {joined}")

    @property
    def armed(self) -> bool:
        return any("merge" in call and "--auto" in call for call in self.calls)


# --------------------------------------------------------------------------
# evaluate_hold
# --------------------------------------------------------------------------


def test_clean_pr_is_not_held() -> None:
    verdict = automerge_guard.evaluate_hold("o/r", 1, runner=FakeGh())
    assert verdict.held is False
    assert verdict.reasons == ()


@pytest.mark.parametrize("label", ["do-not-merge", "blocked", "do-not-automate", "Do-Not-Merge"])
def test_hold_labels_block_arming(label: str) -> None:
    verdict = automerge_guard.evaluate_hold("o/r", 1, runner=FakeGh(labels=[label]))
    assert verdict.held is True
    assert f"`{label.lower()}` label" in verdict.describe()


def test_draft_is_held() -> None:
    verdict = automerge_guard.evaluate_hold("o/r", 1, runner=FakeGh(draft=True))
    assert verdict.held is True
    assert "draft" in verdict.describe()
    assert "mark ready first" in verdict.describe()


def test_human_disarm_after_head_commit_is_held() -> None:
    """The PR #4709 scenario: reviewer disarms, nobody pushes, automation re-arms."""
    verdict = automerge_guard.evaluate_hold("o/r", 4709, runner=FakeGh(disarms=["2026-08-14T04:14:21Z"]))
    assert verdict.held is True
    assert "reviewer disabled auto-merge" in verdict.describe()


def test_human_disarm_before_head_commit_is_not_held() -> None:
    """A push after the disarm supersedes the reviewer's decision."""
    verdict = automerge_guard.evaluate_hold("o/r", 1, runner=FakeGh(disarms=["2026-08-01T00:00:00Z"]))
    assert verdict.held is False


def test_latest_disarm_wins_when_several_exist() -> None:
    verdict = automerge_guard.evaluate_hold(
        "o/r",
        1,
        runner=FakeGh(disarms=["2026-08-01T00:00:00Z", "2026-08-14T04:14:21Z"]),
    )
    assert verdict.held is True


def test_deleted_tracked_files_block_arming() -> None:
    verdict = automerge_guard.evaluate_hold("o/r", 4709, runner=FakeGh(removed=[f"docs/f{i}.md" for i in range(13)]))
    assert verdict.held is True
    assert "deletes 13 tracked file(s)" in verdict.describe()
    assert "+8 more" in verdict.describe()


def test_deletions_released_by_label() -> None:
    verdict = automerge_guard.evaluate_hold(
        "o/r",
        1,
        runner=FakeGh(removed=["a.py"], labels=["deletions-acknowledged"]),
    )
    assert verdict.held is False


@pytest.mark.parametrize("value", ["yes", "true", "YES"])
def test_deletions_released_by_body_marker(value: str) -> None:
    verdict = automerge_guard.evaluate_hold(
        "o/r",
        1,
        runner=FakeGh(removed=["a.py"], body=f"why\n\nDeletions-Acknowledged: {value}\n"),
    )
    assert verdict.held is False


def test_deletions_not_released_by_unrelated_body_text() -> None:
    verdict = automerge_guard.evaluate_hold(
        "o/r",
        1,
        runner=FakeGh(removed=["a.py"], body="Deletions-Acknowledged: not yet"),
    )
    assert verdict.held is True


def test_multiple_signals_are_all_reported() -> None:
    verdict = automerge_guard.evaluate_hold(
        "o/r",
        4709,
        runner=FakeGh(removed=["a.py"], disarms=["2026-08-14T04:14:21Z"]),
    )
    assert len(verdict.reasons) == 2


def test_evaluation_failure_fails_closed() -> None:
    def broken(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return _completed(returncode=1, stderr="HTTP 403 rate limited")

    verdict = automerge_guard.evaluate_hold("o/r", 1, runner=broken)
    assert verdict.held is True
    assert verdict.error is not None and "403" in verdict.error


# --------------------------------------------------------------------------
# arm_auto_merge
# --------------------------------------------------------------------------


def test_arm_refuses_held_pr_and_never_shells_out_to_merge() -> None:
    fake = FakeGh(labels=["do-not-merge"])
    result = automerge_guard.arm_auto_merge("o/r", 1, runner=fake)
    assert result.armed is False
    assert fake.armed is False, "a held PR must never reach `gh pr merge --auto`"


def test_arm_proceeds_on_clean_pr() -> None:
    fake = FakeGh()
    result = automerge_guard.arm_auto_merge("o/r", 1, runner=fake)
    assert result.armed is True
    assert fake.armed is True


def test_arm_passes_strategy_and_delete_branch() -> None:
    fake = FakeGh()
    automerge_guard.arm_auto_merge("o/r", 7, strategy="rebase", delete_branch=True, runner=fake)
    merge_call = next(c for c in fake.calls if "merge" in c)
    assert "--rebase" in merge_call
    assert "--delete-branch" in merge_call
    assert merge_call[:5] == ["gh", "pr", "merge", "7", "--repo"]


def test_arm_reports_gh_failure() -> None:
    fake = FakeGh(merge_returncode=1)
    result = automerge_guard.arm_auto_merge("o/r", 1, runner=fake)
    assert result.armed is False
    assert "merge boom" in result.detail


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_reports_hold_with_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        automerge_guard,
        "evaluate_hold",
        lambda repo, pr, **kw: automerge_guard.HoldVerdict(True, ("`blocked` label",)),
    )
    assert automerge_guard.main(["o/r", "1"]) == 1
    assert "`blocked` label" in capsys.readouterr().out


def test_no_fleet_script_arms_auto_merge_directly() -> None:
    """Regression guard: every arm must route through automerge_guard.

    A direct `gh pr merge --auto` bypasses the reviewer's hold, which is the
    whole defect this module exists to close.
    """
    scripts = _MODULE_PATH.parent
    offenders: list[str] = []
    for path in sorted([*scripts.glob("*.py"), *scripts.glob("*.ps1")]):
        if path.name == _MODULE_PATH.name:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if "pr" in line and "merge" in line and "--auto" in line:
                offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert not offenders, "direct auto-merge arming found:\n" + "\n".join(offenders)


def test_cli_reports_clear_with_zero_exit(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        automerge_guard,
        "evaluate_hold",
        lambda repo, pr, **kw: automerge_guard.HoldVerdict(False),
    )
    assert automerge_guard.main(["o/r", "1"]) == 0
    assert "no hold in effect" in capsys.readouterr().out
