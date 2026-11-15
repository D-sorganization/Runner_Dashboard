"""#4249 B-02: the guard arms only at the head it evaluated, and can require a PASS there.

Hermetic: the fake ``gh`` from ``test_automerge_guard`` records every call.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

import pytest

from tests.test_automerge_guard import (
    HEAD_SHA,
    CloudGh,
    FakeGh,
    _completed,
    _independent_pass_comment,
    automerge_guard,
)

OTHER_SHA = "b" * 40
AUTHOR_LABEL = "agent-model:agy-desk-001"


def _merge_call(fake: FakeGh) -> list[str]:
    return next(call for call in fake.calls if "merge" in call and "--auto" in call)


def _pinned_sha(call: Sequence[str]) -> str:
    return call[call.index("--match-head-commit") + 1]


# --------------------------------------------------------------------------
# The arm is pinned to the head the hold verdict was computed against
# --------------------------------------------------------------------------


def test_arm_pins_the_merge_to_the_evaluated_head() -> None:
    fake = FakeGh()
    result = automerge_guard.arm_auto_merge("o/r", 1, runner=fake)
    assert result.armed is True
    assert _pinned_sha(_merge_call(fake)) == HEAD_SHA


def test_arm_refuses_when_the_head_is_unknown() -> None:
    fake = FakeGh()
    fake.pull["head"]["sha"] = ""  # type: ignore[index]
    result = automerge_guard.arm_auto_merge("o/r", 1, runner=fake)
    assert result.armed is False
    assert fake.armed is False, "an arm that cannot be pinned must not be attempted"


def test_arm_refuses_when_the_callers_head_is_not_the_evaluated_head() -> None:
    fake = FakeGh()
    result = automerge_guard.arm_auto_merge("o/r", 1, match_head_commit=OTHER_SHA, runner=fake)
    assert result.armed is False
    assert "head moved" in result.reason()
    assert fake.armed is False


def test_arm_accepts_a_short_caller_head_that_prefixes_the_evaluated_head() -> None:
    fake = FakeGh()
    full = "a1b2c3d4" * 5
    fake.pull["head"]["sha"] = full  # type: ignore[index]
    result = automerge_guard.arm_auto_merge("o/r", 1, match_head_commit=full[:8], runner=fake)
    assert result.armed is True
    assert _pinned_sha(_merge_call(fake)) == full, "the pin is the full evaluated head, never the short form"


class MovingHeadCloudGh(CloudGh):
    """The head moves between the hold evaluation and the REST fallback arm."""

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(cmd)
        if "--jq" in argv and ".head.sha" in " ".join(argv):
            self.calls.append(argv)
            return _completed(OTHER_SHA)
        return super().__call__(cmd)


def test_rest_fallback_arm_is_revoked_when_the_head_moved() -> None:
    fake = MovingHeadCloudGh()
    result = automerge_guard.arm_auto_merge("o/r", 7, runner=fake)
    assert result.armed is False
    assert "head moved" in result.reason()
    assert any("DELETE" in call for call in fake.rest_calls), "the unpinned REST arm must be revoked"


def test_rest_fallback_arm_stands_when_the_head_did_not_move() -> None:
    fake = CloudGh()
    result = automerge_guard.arm_auto_merge("o/r", 7, runner=fake)
    assert result.armed is True
    assert not any("DELETE" in call for call in fake.rest_calls)


# --------------------------------------------------------------------------
# PASS at the evaluated head
# --------------------------------------------------------------------------


def test_verdict_records_the_head_an_independent_pass_was_recorded_at() -> None:
    fake = FakeGh(labels=[AUTHOR_LABEL], issue_comments=[_independent_pass_comment()])
    verdict = automerge_guard.evaluate_hold("o/r", 1, runner=fake)
    assert verdict.held is False
    assert verdict.pass_head_sha == HEAD_SHA


def test_verdict_records_no_pass_when_none_was_posted() -> None:
    verdict = automerge_guard.evaluate_hold("o/r", 1, runner=FakeGh())
    assert verdict.held is False
    assert verdict.pass_head_sha == ""


def test_a_pass_at_an_older_head_is_not_a_pass_at_this_head() -> None:
    fake = FakeGh(labels=[AUTHOR_LABEL], issue_comments=[_independent_pass_comment(OTHER_SHA)])
    assert automerge_guard.evaluate_hold("o/r", 1, runner=fake).pass_head_sha == ""


def test_require_pass_refuses_a_pr_that_merely_has_no_objection() -> None:
    fake = FakeGh()  # no hold, no review at all
    result = automerge_guard.arm_auto_merge("o/r", 1, require_pass=True, runner=fake)
    assert result.armed is False
    assert "no independent PASS" in result.reason()
    assert fake.armed is False


def test_require_pass_arms_at_the_head_the_pass_was_recorded_at() -> None:
    fake = FakeGh(labels=[AUTHOR_LABEL], issue_comments=[_independent_pass_comment()])
    result = automerge_guard.arm_auto_merge("o/r", 1, require_pass=True, runner=fake)
    assert result.armed is True
    assert _pinned_sha(_merge_call(fake)) == HEAD_SHA


def test_without_require_pass_a_missing_pass_is_reported_but_does_not_block() -> None:
    fake = FakeGh()
    result = automerge_guard.arm_auto_merge("o/r", 1, runner=fake)
    assert result.armed is True
    assert result.pass_missing is True


def test_a_recorded_pass_is_not_reported_as_missing() -> None:
    fake = FakeGh(labels=[AUTHOR_LABEL], issue_comments=[_independent_pass_comment()])
    result = automerge_guard.arm_auto_merge("o/r", 1, runner=fake)
    assert result.armed is True
    assert result.pass_missing is False


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_require_pass_is_read_from_the_environment(value: str) -> None:
    assert automerge_guard.pass_required({"AUTOMERGE_GUARD_REQUIRE_PASS": value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_require_pass_is_off_unless_asked_for(value: str) -> None:
    assert automerge_guard.pass_required({"AUTOMERGE_GUARD_REQUIRE_PASS": value}) is False
    assert automerge_guard.pass_required({}) is False


def test_cli_forwards_the_pin_and_the_pass_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_arm(repo: str, pr: int, **kwargs: object) -> object:
        seen.update(kwargs)
        return automerge_guard.ArmResult(True, automerge_guard.HoldVerdict(False), "armed")

    monkeypatch.setattr(automerge_guard, "arm_auto_merge", fake_arm)
    monkeypatch.setattr(automerge_guard, "_current_head_sha", lambda *_a, **_k: HEAD_SHA)
    monkeypatch.setattr(automerge_guard, "_import_arm_cap_module", lambda: None)
    code = automerge_guard.main(["o/r", "5", "--arm", "--match-head-commit", HEAD_SHA, "--require-pass"])
    assert code == 0
    assert seen["match_head_commit"] == HEAD_SHA
    assert seen["require_pass"] is True


def test_cli_prints_a_shadow_line_when_it_arms_without_a_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AUTOMERGE_GUARD_REQUIRE_PASS", raising=False)
    monkeypatch.setattr(automerge_guard, "_default_runner", FakeGh())
    monkeypatch.setattr(automerge_guard, "_import_arm_cap_module", lambda: None)
    code = automerge_guard.main(["o/r", "5", "--arm"])
    out = capsys.readouterr().out
    assert code == 0
    assert "SHADOW would-block o/r#5: no independent PASS" in out
    assert "armed o/r#5" in out
