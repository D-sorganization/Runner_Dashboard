"""#4249 B-04: a disarm by any account holds the PR, except the guard's own disarms.

Hermetic: the fake ``gh`` from ``test_automerge_guard`` replays a timeline with actors.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from tests.test_automerge_guard import HEAD_SHA, FakeGh, _completed, automerge_guard

AFTER_HEAD = "2026-08-14T04:14:21Z"
BEFORE_HEAD = "2026-08-01T00:00:00Z"
REVIEWER_BOT = "d-sorgcodexagent[bot]"
ARMING_BOT = "d-sorgclaudeagent[bot]"


def _marker_comment(login: str, at: str) -> dict[str, object]:
    return {
        "user": {"login": login},
        "created_at": at,
        "body": f"{automerge_guard.GUARD_DISARM_MARKER}\nAuto-merge disarmed by the merge guard.",
    }


class ActorGh(FakeGh):
    """A timeline whose disarms carry the account that made them."""

    def __init__(
        self,
        *,
        actor_disarms: Sequence[tuple[str, str]] = (),
        comments_fail: bool = False,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.actor_disarms = list(actor_disarms)
        self.comments_fail = comments_fail
        self.posted: list[str] = []

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(cmd)
        joined = " ".join(argv)
        if "/issues/" in joined and "/comments" in joined:
            self.calls.append(argv)
            if "POST" in argv:
                self.posted.append(joined)
                return _completed("{}")
            if self.comments_fail:
                return _completed(returncode=1, stderr="comments boom")
            return _completed(json.dumps(self.issue_comments))
        return super().__call__(cmd)

    def _arm_events(self) -> str:
        rows = [f"{stamp}\tauto_merge_disabled\t{actor}" for stamp, actor in self.actor_disarms]
        legacy = super()._arm_events()
        return "\n".join([*rows, legacy] if legacy else rows)


def _held(fake: FakeGh) -> automerge_guard.HoldVerdict:
    return automerge_guard.evaluate_hold("o/r", 1, runner=fake)


# --------------------------------------------------------------------------
# A disarm by any account holds the PR
# --------------------------------------------------------------------------


def test_the_timeline_query_no_longer_drops_bot_disarms() -> None:
    assert '"Bot"' not in automerge_guard._DISARM_TIMELINE_JQ
    assert ".actor.login" in automerge_guard._DISARM_TIMELINE_JQ


def test_a_bot_reviewer_disarm_after_the_head_arrived_is_held() -> None:
    verdict = _held(ActorGh(actor_disarms=[(AFTER_HEAD, REVIEWER_BOT)]))
    assert verdict.held is True
    assert REVIEWER_BOT in verdict.describe()
    assert "disabled auto-merge" in verdict.describe()


def test_a_bot_disarm_before_the_head_arrived_is_superseded_by_the_push() -> None:
    assert _held(ActorGh(actor_disarms=[(BEFORE_HEAD, REVIEWER_BOT)])).held is False


def test_a_disarm_with_no_recorded_actor_is_still_held() -> None:
    assert _held(ActorGh(actor_disarms=[(AFTER_HEAD, "")])).held is True


# --------------------------------------------------------------------------
# The guard's own disarms are not a reviewer decision
# --------------------------------------------------------------------------


def test_the_hold_guard_workflow_disarm_is_not_a_reviewer_decision() -> None:
    assert _held(ActorGh(actor_disarms=[(AFTER_HEAD, "github-actions[bot]")])).held is False


def test_a_disarm_the_guard_marked_as_its_own_is_not_held() -> None:
    fake = ActorGh(
        actor_disarms=[(AFTER_HEAD, ARMING_BOT)],
        issue_comments=[_marker_comment(ARMING_BOT, "2026-08-14T04:14:24Z")],
    )
    assert _held(fake).held is False


def test_a_marker_from_another_account_does_not_excuse_the_disarm() -> None:
    fake = ActorGh(
        actor_disarms=[(AFTER_HEAD, REVIEWER_BOT)],
        issue_comments=[_marker_comment(ARMING_BOT, "2026-08-14T04:14:24Z")],
    )
    assert _held(fake).held is True


def test_a_marker_long_after_the_disarm_does_not_excuse_it() -> None:
    fake = ActorGh(
        actor_disarms=[(AFTER_HEAD, ARMING_BOT)],
        issue_comments=[_marker_comment(ARMING_BOT, "2026-08-14T05:30:00Z")],
    )
    assert _held(fake).held is True


def test_one_genuine_disarm_among_guard_disarms_is_held() -> None:
    fake = ActorGh(
        actor_disarms=[(AFTER_HEAD, ARMING_BOT), ("2026-08-14T06:00:00Z", REVIEWER_BOT)],
        issue_comments=[_marker_comment(ARMING_BOT, "2026-08-14T04:14:24Z")],
    )
    verdict = _held(fake)
    assert verdict.held is True
    assert REVIEWER_BOT in verdict.describe()


def test_unreadable_comments_fail_closed_when_a_disarm_needs_checking() -> None:
    fake = ActorGh(actor_disarms=[(AFTER_HEAD, ARMING_BOT)], comments_fail=True)
    assert _held(fake).held is True


def test_comments_are_not_read_for_the_disarm_signal_when_no_disarm_is_pending() -> None:
    fake = ActorGh(actor_disarms=[(BEFORE_HEAD, REVIEWER_BOT)])
    assert automerge_guard._disarm_reasons(fake, "o/r", 1, HEAD_SHA) == []
    assert not any("/comments" in " ".join(call) for call in fake.calls)


# --------------------------------------------------------------------------
# The guard marks its own disarms
# --------------------------------------------------------------------------


def test_disarm_if_held_marks_its_disarm_with_the_hold_reasons() -> None:
    fake = ActorGh(labels=["do-not-merge"])
    verdict = automerge_guard.disarm_if_held("o/r", 7, runner=fake)
    assert verdict.held is True
    assert len(fake.posted) == 1
    assert automerge_guard.GUARD_DISARM_MARKER in fake.posted[0]
    assert "do-not-merge" in fake.posted[0]


def test_disarm_if_held_posts_no_marker_when_nothing_was_disarmed() -> None:
    fake = ActorGh(labels=["do-not-merge"], disarm_on_delete=False)
    automerge_guard.disarm_if_held("o/r", 7, runner=fake)
    assert fake.posted == []


def test_disarm_if_held_posts_no_marker_on_a_clean_pr() -> None:
    fake = ActorGh()
    assert automerge_guard.disarm_if_held("o/r", 7, runner=fake).held is False
    assert fake.posted == []


def test_revoking_a_rest_arm_is_marked_as_the_guards_own_disarm() -> None:
    fake = ActorGh()
    message, failed = automerge_guard._revoke_rest_arm("o/r", 7, fake)
    assert failed is False
    assert "revoked" in message
    assert len(fake.posted) == 1
    assert automerge_guard.GUARD_DISARM_MARKER in fake.posted[0]


@pytest.mark.parametrize("returncode", [1])
def test_a_marker_that_cannot_be_posted_does_not_fail_the_disarm(returncode: int) -> None:
    class NoPostGh(ActorGh):
        def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if "POST" in cmd:
                self.calls.append(list(cmd))
                return _completed(returncode=returncode, stderr="403")
            return super().__call__(cmd)

    fake = NoPostGh(labels=["do-not-merge"])
    assert automerge_guard.disarm_if_held("o/r", 7, runner=fake).held is True
