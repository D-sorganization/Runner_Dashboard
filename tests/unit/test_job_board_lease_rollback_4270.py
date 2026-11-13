"""#4270 WP15 B-08 on the job-board path: a failed lease post leaves no orphan ``claim:*`` label.

``jobs.claim_job`` posts through ``claims.post_lease``; a post that set the label but never
posted the lease comment must be rolled back (label removed, retried, failure reported).
``heartbeat_job`` shares ``post_lease`` and must never release the lease it is renewing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coordination import claim_state as claim_state_mod
from coordination import claims as claims_mod
from coordination import job_board_lease
from coordination import jobs as jobs_mod
from coordination.board import RMScriptError

from tests.api.coordination_fake_rm import (
    FakeRM,
    claim_status,
    install,
    lease_result,
    patch_gh_list_fetch,
    release_result,
)

REPO = "Tools"
AGENT = "codex"
_COMMENT_FAILED = ["comment: CalledProcessError: HTTP 502"]


def _open_issue_fetch(number: int) -> Any:
    payload = {"number": number, "state": "open", "labels": [], "title": "b-08", "created_at": "2026-11-11T00:00:00Z"}
    return lambda path: payload if path.endswith(f"/issues/{number}") else []


def _releases(rm: FakeRM) -> list[list[str]]:
    return [argv for name, argv in rm.calls() if name == "release_agent_lease"]


def _lease_row(issue: int) -> tuple[Any, ...] | None:
    return job_board_lease._lease_state.get(job_board_lease._lease_key(REPO, issue))


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    yield from install(tmp_path, monkeypatch, {}, repos=REPO)


def _claim(issue: int) -> dict[str, Any]:
    return jobs_mod.claim_job(REPO, issue, agent=AGENT, session=f"{AGENT}-{issue}", intent="ship")


@pytest.mark.unit
def test_job_claim_failed_lease_comment_removes_the_label_it_added(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    issue = 4301
    patch_gh_list_fetch(monkeypatch, _open_issue_fetch(issue))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(label=True, comment=False, errors=_COMMENT_FAILED), rc=1)
    rm.respond("release_agent_lease", release_result())

    with pytest.raises(RMScriptError, match="HTTP 502"):
        _claim(issue)

    releases = _releases(rm)
    assert len(releases) == 1, "the orphan claim: label must be removed exactly once"
    assert releases[0][releases[0].index("--session") + 1] == f"{AGENT}-{issue}"
    assert releases[0][releases[0].index("--agent") + 1] == AGENT
    assert jobs_mod._current_fence(REPO, issue) == 0
    assert _lease_row(issue) is None
    assert claim_state_mod.get_store().read(REPO, issue) is None


@pytest.mark.unit
def test_job_claim_rollback_failure_is_retried_and_reported(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    issue = 4302
    patch_gh_list_fetch(monkeypatch, _open_issue_fetch(issue))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(label=True, comment=False, errors=_COMMENT_FAILED), rc=1)
    rm.respond("release_agent_lease", release_result(label=False, comment=False, errors=["label: HTTP 500"]), rc=1)

    with pytest.raises(RMScriptError) as excinfo:
        _claim(issue)

    message = str(excinfo.value)
    assert "HTTP 502" in message, "the original lease failure stays in the error"
    assert "rollback failed" in message and "HTTP 500" in message
    assert len(_releases(rm)) == claims_mod.ROLLBACK_ATTEMPTS > 1
    assert jobs_mod._current_fence(REPO, issue) == 0
    assert _lease_row(issue) is None
    assert claim_state_mod.get_store().read(REPO, issue) is None


_NOTHING_TO_RELEASE = {
    "label_updated": False,
    "comment_posted": False,
    "receipt": "",
    "errors": [],
    "already_released": True,
    "ok": True,
}


@pytest.mark.unit
def test_job_claim_rollback_deletes_the_label_when_rm_has_nothing_to_release(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RM's real answer here: no lease comment exists, so its release is a no-op. RD deletes the label."""
    issue = 4305
    patch_gh_list_fetch(monkeypatch, _open_issue_fetch(issue))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(label=True, comment=False, errors=_COMMENT_FAILED), rc=1)
    rm.respond("release_agent_lease", _NOTHING_TO_RELEASE)
    removed: list[tuple[str, int, str]] = []
    monkeypatch.setattr(claims_mod.gh_cli, "remove_issue_label", lambda *args: removed.append(args))

    with pytest.raises(RMScriptError) as excinfo:
        _claim(issue)

    message = str(excinfo.value)
    assert "HTTP 502" in message and "rollback failed" not in message
    assert removed == [(f"D-sorganization/{REPO}", issue, f"claim:{AGENT}")]
    assert len(_releases(rm)) == 1


@pytest.mark.unit
def test_job_claim_rollback_reports_a_label_it_could_not_delete(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    issue = 4307
    patch_gh_list_fetch(monkeypatch, _open_issue_fetch(issue))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(label=True, comment=False, errors=_COMMENT_FAILED), rc=1)
    rm.respond("release_agent_lease", _NOTHING_TO_RELEASE)
    attempts: list[int] = []

    def fail_delete(*_args: Any) -> None:
        attempts.append(1)
        raise RuntimeError("HTTP 500 from label delete")

    monkeypatch.setattr(claims_mod.gh_cli, "remove_issue_label", fail_delete)

    with pytest.raises(RMScriptError) as excinfo:
        _claim(issue)

    message = str(excinfo.value)
    assert "HTTP 502" in message
    assert "rollback failed" in message and "HTTP 500 from label delete" in message
    assert len(attempts) == claims_mod.ROLLBACK_ATTEMPTS


@pytest.mark.unit
def test_job_claim_post_without_output_still_attempts_rollback(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    """A post that died without JSON (timeout, crash) may have written the label first."""
    issue = 4306
    patch_gh_list_fetch(monkeypatch, _open_issue_fetch(issue))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", None, rc=1, stderr="timed out")
    rm.respond("release_agent_lease", release_result())

    with pytest.raises(RMScriptError):
        _claim(issue)

    assert len(_releases(rm)) == 1
    assert jobs_mod._current_fence(REPO, issue) == 0


@pytest.mark.unit
def test_job_claim_failed_post_without_label_does_not_release(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a label this call added is removed: no label written → no release call."""
    issue = 4303
    patch_gh_list_fetch(monkeypatch, _open_issue_fetch(issue))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(label=False, comment=False, errors=["HTTP 502"]), rc=1)

    with pytest.raises(RMScriptError, match="HTTP 502"):
        _claim(issue)

    assert _releases(rm) == []
    assert jobs_mod._current_fence(REPO, issue) == 0


@pytest.mark.unit
def test_heartbeat_failed_post_reports_and_keeps_the_existing_lease(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = 4304
    session = f"{AGENT}-{issue}"
    patch_gh_list_fetch(monkeypatch, _open_issue_fetch(issue))
    rm.respond("post_agent_lease", lease_result())
    posted = {"n": 0}
    real_post_lease = claims_mod.post_lease

    def post_lease(*args: Any, **kwargs: Any) -> dict[str, Any]:
        posted["n"] += 1
        return real_post_lease(*args, **kwargs)

    def check(repo: str, number: int) -> dict[str, Any]:
        if posted["n"] == 0:
            return {"available": True, "held": False, "agent": "", "session": "", "reason": ""}
        return {"available": True, "held": True, "agent": AGENT, "session": session, "reason": "active lease"}

    monkeypatch.setattr(claims_mod, "post_lease", post_lease)
    monkeypatch.setattr(claims_mod, "check", check)
    claimed = _claim(issue)
    before = _lease_row(issue)
    assert before is not None

    rm.respond("post_agent_lease", lease_result(label=True, comment=False, errors=_COMMENT_FAILED), rc=1)
    rm.respond("release_agent_lease", release_result())
    with pytest.raises(RMScriptError, match="HTTP 502"):
        jobs_mod.heartbeat_job(REPO, issue, agent=AGENT, session=session, fence=claimed["fence"])

    assert _releases(rm) == [], "a failed heartbeat must not release the lease it renews"
    assert _lease_row(issue) == before
    assert jobs_mod._current_fence(REPO, issue) == claimed["fence"]
    row = claim_state_mod.get_store().read(REPO, issue)
    assert row is not None and (row.agent, row.session) == (AGENT, session)
