"""#4251: the merge-shepherd disarm route must be one GitHub actually serves.

Measured against Runner_Dashboard with the fleet App identity on 2026-11-28:

* ``gh api -X DELETE repos/<repo>/pulls/<n>/ccr/auto_merge`` -> ``404 Not Found``
  (the route does not exist, so every disarm through it fails);
* ``gh pr merge <n> --repo <repo> --disable-auto`` -> success, and it is idempotent
  when nothing is armed.

The scheduled Merge shepherd pass had therefore failed 17 runs in a row while
holding/do-not-merge PRs stayed auto-merge armed.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

import scripts.merge_shepherd_pass as msp
from scripts import automerge_guard
from tests.unit.test_merge_shepherd_disarm_executor_4618 import (
    ShepherdDisarmGh,
    _completed,
)

DEAD_ROUTE_STDERR = "gh: Not Found (HTTP 404)"


class LiveGitHubGh(ShepherdDisarmGh):
    """Replays the routes exactly as GitHub answers them today."""

    def __init__(self, *, cli_disarm_works: bool = True, **kw: Any) -> None:
        super().__init__(**kw)
        self.cli_disarm_works = cli_disarm_works
        self.dead_route_calls = 0
        self.cli_disarm_calls = 0

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(cmd)
        joined = " ".join(argv)
        if "pr" in argv and "merge" in argv and "--disable-auto" in joined:
            self.calls.append(argv)
            self.cli_disarm_calls += 1
            if self.cli_disarm_works:
                self.mark_disarmed()
                return _completed(stdout="✓ Auto-merge disabled for pull request o/r#7\n")
            return _completed(returncode=1, stderr="graphql blocked")
        if "/ccr/auto_merge" in joined and "DELETE" in joined:
            self.calls.append(argv)
            self.dead_route_calls += 1
            return _completed(returncode=1, stderr=DEAD_ROUTE_STDERR)
        return super().__call__(argv)

    def mark_disarmed(self) -> None:
        self.deleted = True
        self.auto_merge_on = False
        self.in_merge_queue = False


def _guard_module() -> Any:
    import importlib
    import sys

    scripts_dir = str(msp.Path(__file__).resolve().parents[2] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("automerge_guard")


def test_disarm_succeeds_on_the_live_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 on the legacy REST route must not mean the PR could not be disarmed."""
    fake = LiveGitHubGh(auto_merge_on=True)
    assert automerge_guard._disarm_auto_merge("o/r", 7, fake) is True
    assert fake.cli_disarm_calls == 1


def test_disarm_reports_which_route_it_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = LiveGitHubGh(auto_merge_on=True)
    automerge_guard._disarm_auto_merge("o/r", 7, fake)
    assert not any("/ccr/auto_merge" in " ".join(call) for call in fake.calls), (
        "the dead REST route should not be called when the supported route succeeded"
    )


def test_disarm_fails_closed_when_no_supported_route_works() -> None:
    fake = LiveGitHubGh(auto_merge_on=True, cli_disarm_works=False)
    assert automerge_guard._disarm_auto_merge("o/r", 7, fake) is False
    assert fake.auto_merge_on is True


def test_disarm_for_shepherd_leaves_no_arm_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **kw: True)
    fake = LiveGitHubGh(auto_merge_on=True, in_merge_queue=False)
    before, after, _verdict = automerge_guard.disarm_for_shepherd(
        "o/r",
        7,
        detail="rejected_at_head",
        head_sha="a" * 40,
        revalidate=lambda: True,
        runner=fake,
    )
    assert before.auto_merge_on is True
    assert automerge_guard.disposition_cleared(after) is True
    assert any("comments" in " ".join(call) for call in fake.calls), (
        "a completed disarm still has to leave the guard's disarm marker"
    )


def test_shepherd_executor_reports_disarmed_on_the_live_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pass exits non-zero on ``disarmed=False``; that was true for every PR."""
    monkeypatch.setattr(msp, "_revalidate_disarm", lambda **kw: True)
    fake = LiveGitHubGh(auto_merge_on=True)
    guard_mod = _guard_module()
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **kw: True)
    monkeypatch.setattr(
        guard_mod,
        "disarm_for_shepherd",
        lambda *a, **k: automerge_guard.disarm_for_shepherd(*a, **k, runner=fake),
    )
    result = msp._disarm_via_guard("o/r", 7, repo_root=msp.Path.cwd(), detail="rejected_at_head", head_sha="a" * 40)
    assert result["disarmed"] is True
    assert result["exit_code"] == 0
    assert result["after_auto_merge"] is False
