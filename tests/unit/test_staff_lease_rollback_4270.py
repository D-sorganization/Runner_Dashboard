"""#4270 B-08 on the staff dispatch path: failed lease post must not leave an orphan label.

``staff.lease.acquire`` posts through RM ``post_agent_lease``. When the label is set but the
lease comment never lands, the run must still block (B-06) and the orphan ``claim:*`` label must
be rolled back the same way as ``coordination.claims`` / job-board claim (#4411).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from staff.store import RunRecord, RunStore

from staff import claim_guard
from staff import lease as lease_mod
from tests.api.coordination_fake_rm import FakeRM, claim_status, lease_result, release_result

REPO = "Runner_Dashboard"
ISSUE = "4270"
AGENT = "cursor"
RUN_ID = "run-4270-staff-b08"
_SESSION = f"staff-{RUN_ID}"
_COMMENT_FAILED = ["comment: CalledProcessError: HTTP 502"]


def _releases(rm: FakeRM) -> list[list[str]]:
    return [argv for name, argv in rm.calls() if name == "release_agent_lease"]


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRM:
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path / "rm"))
    monkeypatch.setenv("STAFF_RM_PYTHON", sys.executable)
    claim_guard.reset_applied()
    fake = FakeRM(tmp_path / "rm")
    return fake


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunStore:
    path = tmp_path / "runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(path))
    return RunStore(path)


def _seed(store: RunStore) -> None:
    store.create_run(
        RunRecord(
            id=RUN_ID,
            role="issue-remediator",
            provider=AGENT,
            model=None,
            machine="test",
            repo=REPO,
            target_kind="issue",
            target_ref=f"#{ISSUE}",
            prompt="wp15",
            branch="cursor/4270-staff-b08",
        )
    )


_NOTHING_TO_RELEASE = {
    "label_updated": False,
    "comment_posted": False,
    "receipt": "",
    "errors": [],
    "already_released": True,
    "ok": True,
}


@pytest.mark.unit
def test_staff_acquire_rolls_back_orphan_label_when_comment_post_fails(
    rm: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm.root))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond(
        "post_agent_lease",
        lease_result(label=True, comment=False, errors=_COMMENT_FAILED, agent=AGENT),
        rc=1,
    )
    rm.respond("release_agent_lease", release_result())
    _seed(store)

    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="cursor/4270-staff-b08")

    assert note is None
    rec = store.get_run(RUN_ID)
    assert rec is not None and rec.failure_class == "lease_blocked"
    releases = _releases(rm)
    assert len(releases) == 1
    assert releases[0][releases[0].index("--agent") + 1] == AGENT
    assert "agent_communicate" not in [name for name, _ in rm.calls()]


@pytest.mark.unit
def test_staff_acquire_deletes_label_when_rm_reports_nothing_to_release(
    rm: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coordination.claims as claims_mod

    monkeypatch.setenv("STAFF_RM_ROOT", str(rm.root))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond(
        "post_agent_lease",
        lease_result(label=True, comment=False, errors=_COMMENT_FAILED, agent=AGENT),
        rc=1,
    )
    rm.respond("release_agent_lease", _NOTHING_TO_RELEASE)
    removed: list[tuple[str, int, str]] = []
    monkeypatch.setattr(claims_mod.gh_cli, "remove_issue_label", lambda *args: removed.append(args))
    _seed(store)

    lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="cursor/4270-staff-b08")

    assert removed == [(f"D-sorganization/{REPO}", int(ISSUE), f"claim:{AGENT}")]


@pytest.mark.unit
def test_staff_acquire_does_not_release_when_post_failed_without_label(
    rm: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm.root))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond(
        "post_agent_lease",
        lease_result(label=False, comment=False, errors=["HTTP 502"], agent=AGENT),
        rc=1,
    )
    _seed(store)

    lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="cursor/4270-staff-b08")

    assert _releases(rm) == []


@pytest.mark.unit
def test_staff_acquire_attempts_rollback_when_post_returns_no_json(
    rm: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm.root))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", None, rc=1, stderr="timed out")
    rm.respond("release_agent_lease", release_result())
    _seed(store)

    lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="cursor/4270-staff-b08")

    assert len(_releases(rm)) == 1
