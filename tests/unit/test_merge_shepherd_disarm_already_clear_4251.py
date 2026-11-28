"""#4251: a shepherd-planned disarm that is already satisfied must not cost a GitHub round trip.

#4737 taught the guard which disposition to clear, but the executor still runs the whole
guard round trip — hold evaluation plus a repository-wide revalidation GraphQL — before it
ever looks at that disposition. For a PR that is neither armed nor queued there is nothing
to clear, so all of that read work can only fail, and a transient failure is a red main.

Measured on ``main`` at ``dd10a47f`` (Merge shepherd run 46139657286, 2026-11-28T11:23Z),
five red runs in a row, 1198-1202:

    merge shepherd disarm repo=D-sorganization/UpstreamDrift pr=15922 disarmed=False exit_code=1
    merge shepherd disarm repo=D-sorganization/UpstreamDrift pr=15939 disarmed=False exit_code=1
    merge shepherd disarm repo=D-sorganization/UpstreamDrift pr=15944 disarmed=False exit_code=1
      {"pr": 15922, "error": "gh: HTTP 502", "disarmed": false, "exit_code": 1}
      {"pr": 15939, "error": "unexpected end of JSON input", "disarmed": false, "exit_code": 1}
      {"pr": 15944, "error": "gh: HTTP 502", "disarmed": false, "exit_code": 1}

All three were planned for ``incomplete_check_page``, and all three already reported
``autoMergeRequest: null`` with ``mergeStateStatus: CLEAN`` at exactly the planned head
(``gh pr view 15922 --json autoMergeRequest,mergeStateStatus,headRefOid``), so the intended
end state was in place and no route call was ever needed. The pass still went red because
the reads in front of that no-op failed — and those reads are the same heavy UpstreamDrift
GraphQL that produced run 1197's ``Resource limits for this query exceeded`` error, so every
pointless revalidation made the next ``incomplete_check_page`` more likely.

Fail-closed is preserved: a PR that really is armed still runs the guard, and a guard that
cannot revalidate it still reports ``disarmed=False``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import scripts.merge_shepherd_pass as msp
from scripts import automerge_guard
from tests.unit.test_automerge_guard_disarm_route_4251 import LiveGitHubGh
from tests.unit.test_merge_shepherd_disarm_revalidate_4251 import _NotHeld

UD = "D-sorganization/UpstreamDrift"
# The three rows Merge shepherd run 46139657286 planned for disarm. The heads are synthetic in
# the shape the 4618 executor tests already use: the disposition read never consults them, and a
# literal 40-hex value reads as a high-entropy string to detect-secrets, which drifts the baseline.
SATISFIED_PLAN = (
    (15922, "a" * 40),
    (15939, "b" * 40),
    (15944, "c" * 40),
)
TRANSIENT = OSError("gh: HTTP 502")


def _guard_module() -> Any:
    import importlib
    import sys

    scripts_dir = str(msp.Path(__file__).resolve().parents[2] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("automerge_guard")


def _patch_gh(monkeypatch: pytest.MonkeyPatch, fake: LiveGitHubGh) -> None:
    """Make the bare ``automerge_guard`` module the executor imports use the fake runner."""
    guard_mod = _guard_module()
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **kw: True)
    monkeypatch.setattr(guard_mod, "check_token_identity", lambda **kw: True)
    monkeypatch.setattr(automerge_guard, "evaluate_hold", lambda *a, **kw: _NotHeld())
    monkeypatch.setattr(guard_mod, "evaluate_hold", lambda *a, **kw: _NotHeld())
    monkeypatch.setattr(
        guard_mod,
        "disarm_for_shepherd",
        lambda *args, **kw: automerge_guard.disarm_for_shepherd(*args, **kw, runner=fake),
    )
    monkeypatch.setattr(
        guard_mod,
        "read_merge_disposition",
        lambda *args, **kw: automerge_guard.read_merge_disposition(*args, **kw, runner=fake),
    )


def _revalidation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The revalidation GraphQL fails the way GitHub answered run 1202."""

    def _boom(**_kw: object) -> bool:
        raise TRANSIENT

    monkeypatch.setattr(msp, "_revalidate_disarm", _boom)


def test_already_cleared_disposition_never_reaches_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing armed and nothing queued: no route call, no hold read, no revalidation GraphQL."""
    fake = LiveGitHubGh(auto_merge_on=False, in_merge_queue=False)
    _patch_gh(monkeypatch, fake)
    _revalidation_fails(monkeypatch)

    result = msp._disarm_via_guard(
        UD,
        15922,
        repo_root=msp.Path.cwd(),
        head_sha=SATISFIED_PLAN[0][1],
        detail="incomplete_check_page",
    )

    assert result["disarmed"] is True, f"satisfied plan reported as a failure: {result}"
    assert result["exit_code"] == 0
    assert fake.cli_disarm_calls == 0
    assert fake.dead_route_calls == 0
    assert not fake.comments, "a PR the guard never touched must not collect a disarm marker"


def test_armed_pr_still_fails_closed_when_revalidation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """The skip is only for satisfied plans: a real arm with an unreadable head stays red."""
    fake = LiveGitHubGh(auto_merge_on=True, in_merge_queue=False)
    _patch_gh(monkeypatch, fake)
    _revalidation_fails(monkeypatch)

    result = msp._disarm_via_guard(
        UD,
        15922,
        repo_root=msp.Path.cwd(),
        head_sha=SATISFIED_PLAN[0][1],
        detail="incomplete_check_page",
    )

    assert result["disarmed"] is False
    assert result["exit_code"] == 1
    assert result["error"] == "gh: HTTP 502"
    assert fake.cli_disarm_calls == 0, "an unverified head must not be disarmed blind"


def test_scheduled_pass_is_green_when_the_planned_disarms_are_satisfied(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Merge shepherd run 1202's payload, replayed: the pass must exit 0, not 1."""
    from coordination.merge_shepherd_pass import PassSummary

    summary = PassSummary()
    summary.disarmed = [
        {
            "repo": UD,
            "pr": pr,
            "head_sha": sha,
            "strategy": "disarm",
            "dry_run": False,
            "detail": "incomplete_check_page",
        }
        for pr, sha in SATISFIED_PLAN
    ]
    fake = LiveGitHubGh(auto_merge_on=False, in_merge_queue=False)
    _patch_gh(monkeypatch, fake)
    _revalidation_fails(monkeypatch)
    monkeypatch.setattr(msp, "run_merge_shepherd_pass", lambda **kw: summary)
    monkeypatch.setattr(msp, "_resolve_hub_holds", lambda: ([], None))
    monkeypatch.setattr(msp, "resolve_live_arming", lambda **kw: True)

    code = msp.main(["--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0, f"the scheduled pass still exits {code}: {payload}"
    assert [row["pr"] for row in payload["executed_disarms"]] == [pr for pr, _ in SATISFIED_PLAN]
    assert all(row["already_clear"] for row in payload["executed_disarms"]), payload["executed_disarms"]
    mutations = [row for row in fake.calls if "--disable-auto" in row or "DELETE" in row]
    assert not mutations, f"the pass tried to disarm PRs that were already clear: {mutations}"
