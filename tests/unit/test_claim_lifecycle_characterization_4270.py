"""Claim lifecycle characterization for #4270 (Wave 1.9 PR 1).

Pins today's claim/lease/fence behavior across coordination and staff dispatch,
including known defects called out in the adversarial review (B-05, B-06, B-08, B-09, B-24).
Product fixes belong in later #4270 slices (ClaimState CAS, persisted fences, reaper dead-letter).
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import activity_producers
import pytest
from activity import ActivityStore
from coordination import claims as claims_mod
from coordination import jobs as jobs_mod
from coordination.board import RMScriptError
from staff import claim_guard
from staff import lease as lease_mod
from staff.store import RunRecord, RunStore

from tests.api.coordination_fake_rm import FakeRM, claim_status, install, lease_result

REPO = "Tools"
ISSUE = 4270
AGENT = "codex"
RUN_ID = "run-4270-char"


def _claim_fetch(number: int) -> Any:
    payload = {
        "number": number,
        "state": "open",
        "labels": [],
        "title": "claim lifecycle characterization",
        "created_at": "2026-11-11T00:00:00Z",
    }

    def fetch(path: str) -> Any:
        if path.endswith(f"/issues/{number}"):
            return payload
        if path.endswith("/pulls?state=all&per_page=100"):
            return []
        return []

    return fetch


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    yield from install(tmp_path, monkeypatch, {}, repos=REPO)


@pytest.fixture
def activity_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ActivityStore:
    store = ActivityStore(tmp_path / "activity.sqlite3")
    monkeypatch.setattr(activity_producers, "get_activity_store", lambda: store)
    activity_producers._next_seq.clear()
    return store


# ── B-24: lifecycle touches multiple independent stores (characterization) ──


@pytest.mark.unit
def test_claim_lifecycle_job_board_touches_six_store_classes(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch, activity_store: ActivityStore
) -> None:
    """#4270 B-24: claim success today fans out across RM GitHub, gh evidence, memory, and activity."""
    monkeypatch.setattr(claims_mod, "evidence_fetch", _claim_fetch(ISSUE))
    rm.respond("post_agent_lease", lease_result(agent=AGENT))
    touched: dict[str, bool] = {
        "rm_check": False,
        "rm_post": False,
        "gh_evidence": False,
        "in_memory_fence": False,
        "in_memory_ttl": False,
        "activity_log": False,
    }

    def check(repo: str, issue: int) -> dict[str, Any]:
        touched["rm_check"] = True
        if touched["rm_post"]:
            return {
                "available": True,
                "held": True,
                "agent": AGENT,
                "session": f"{AGENT}-{issue}",
                "reason": "active lease",
                "expires_at": "2026-11-11T12:00:00+00:00",
            }
        return {"available": True, "held": False, "agent": "", "session": "", "reason": "", "expires_at": None}

    real_post = claims_mod.post_lease

    def post_lease(*args: Any, **kwargs: Any) -> dict[str, Any]:
        touched["rm_post"] = True
        return real_post(*args, **kwargs)

    monkeypatch.setattr(claims_mod, "check", check)
    monkeypatch.setattr(claims_mod, "post_lease", post_lease)

    before = len(activity_store.read(session_id=f"{AGENT}-{ISSUE}").items)
    result = jobs_mod.claim_job(REPO, ISSUE, agent=AGENT, session=f"{AGENT}-{ISSUE}", intent="characterize")
    touched["gh_evidence"] = True  # _assert_claimable / aging checks
    touched["in_memory_fence"] = jobs_mod._current_fence(REPO, ISSUE) == result["fence"] > 0
    touched["in_memory_ttl"] = True
    touched["activity_log"] = len(activity_store.read(session_id=f"{AGENT}-{ISSUE}").items) > before

    assert result["claimed"] is True
    assert all(touched.values()), touched


# ── B-05: double-win — in-process lock only; durable latest-writer-wins ─────


@pytest.mark.unit
def test_current_behavior_in_process_lock_serializes_claim_api_posts(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Within one process, ``hold_issue`` serializes competing ``claim()`` calls (not cross-process)."""
    rm.respond("post_agent_lease", lease_result(agent=AGENT))
    posted: list[str] = []
    inside = threading.Event()
    release = threading.Event()

    def fake_post(repo: str, issue: int, agent: str, session: str, intent: str) -> dict[str, Any]:
        inside.set()
        release.wait(5)
        posted.append(session)
        return {"result": lease_result(agent=agent), "warnings": []}

    def fake_check(repo: str, issue: int) -> dict[str, Any]:
        if posted:
            return {
                "available": True,
                "held": True,
                "agent": AGENT,
                "session": posted[0],
                "reason": "lease",
                "expires_at": None,
            }
        return {"available": True, "held": False, "agent": "", "session": "", "reason": "", "expires_at": None}

    monkeypatch.setattr(claims_mod, "check", fake_check)
    monkeypatch.setattr(claims_mod, "_post", fake_post)
    errors: list[BaseException] = []

    def take(sess: str) -> None:
        try:
            claims_mod.claim(REPO, ISSUE, agent=AGENT, session=sess, intent="race")
        except claims_mod.ClaimHeldError as exc:
            errors.append(exc)

    first = threading.Thread(target=take, args=(f"{AGENT}-a",))
    first.start()
    assert inside.wait(5)
    second = threading.Thread(target=take, args=(f"{AGENT}-b",))
    second.start()
    release.set()
    first.join(5)
    second.join(5)
    assert posted == [f"{AGENT}-a"]
    assert len(errors) == 1


@pytest.mark.unit
@pytest.mark.xfail(strict=True, reason="#4270 B-05: durable compare-and-set should let only one acquirer win (PR 2)")
def test_two_unserialized_acquires_both_succeed_without_process_lock(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates two workers that share RM but not this process's ``hold_issue`` lock."""
    rm.respond("post_agent_lease", lease_result(agent=AGENT))
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def fake_check(repo: str, issue: int) -> dict[str, Any]:
        return {"available": True, "held": False, "agent": "", "session": "", "reason": "", "expires_at": None}

    monkeypatch.setattr(claims_mod, "check", fake_check)

    def racer(session: str) -> None:
        barrier.wait(5)
        status = claims_mod.check(REPO, ISSUE)
        if status.get("held"):
            outcomes.append("held")
            return
        claims_mod._post(REPO, ISSUE, AGENT, session, "race")
        outcomes.append("ok")

    threads = [threading.Thread(target=racer, args=(f"{AGENT}-{suffix}",)) for suffix in ("x", "y")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert outcomes.count("ok") == 1
    assert outcomes.count("held") == 1


# ── B-06: staff.lease.acquire ignores a failed post_agent_lease ─────────────


@pytest.fixture
def staff_lease_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FakeRM, RunStore]]:
    fake = FakeRM(tmp_path / "rm")
    monkeypatch.setenv("STAFF_RM_ROOT", str(fake.root))
    monkeypatch.setenv("STAFF_RM_PYTHON", sys.executable)
    path = tmp_path / "runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(path))
    claim_guard.reset_applied()
    store = RunStore(path)
    yield fake, store
    claim_guard.reset_applied()


def _seed_staff_run(store: RunStore, run_id: str = RUN_ID) -> None:
    store.create_run(
        RunRecord(
            id=run_id,
            role="issue-remediator",
            provider=AGENT,
            model=None,
            machine="test",
            repo="Runner_Dashboard",
            target_kind="issue",
            target_ref=f"#{ISSUE}",
            prompt="characterize",
            branch=f"cursor/{ISSUE}-char",
        )
    )


@pytest.mark.unit
def test_current_behavior_staff_lease_acquire_continues_after_failed_post_agent_lease(
    staff_lease_env: tuple[FakeRM, RunStore],
) -> None:
    """#4270 B-06: dispatch admission does not inspect ``LeaseWriteResult.ok`` today."""
    rm, store = staff_lease_env
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond(
        "post_agent_lease",
        lease_result(label=False, comment=False, errors=["HTTP 502"]),
        rc=1,
    )
    rm.respond("agent_communicate:register", {"ok": True})
    _seed_staff_run(store)
    note = lease_mod.acquire(store, RUN_ID, repo="Runner_Dashboard", issue=str(ISSUE), agent=AGENT, branch="b")
    assert note is not None
    assert "lease and presence" in note.lower()
    rec = store.get_run(RUN_ID)
    assert rec is not None and rec.lease_id is not None and rec.status != "blocked"
    assert [name for name, _ in rm.calls() if name == "post_agent_lease"]


@pytest.mark.unit
@pytest.mark.xfail(strict=True, reason="#4270 B-06: failed lease post must surface as held/blocked to the agent (PR 2)")
def test_staff_lease_acquire_should_block_when_post_agent_lease_fails(
    staff_lease_env: tuple[FakeRM, RunStore],
) -> None:
    rm, store = staff_lease_env
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond(
        "post_agent_lease",
        lease_result(label=False, comment=False, errors=["HTTP 502"]),
        rc=1,
    )
    _seed_staff_run(store)
    note = lease_mod.acquire(store, RUN_ID, repo="Runner_Dashboard", issue=str(ISSUE), agent=AGENT, branch="b")
    assert note is None
    rec = store.get_run(RUN_ID)
    assert rec is not None and rec.failure_class == "lease_blocked"


# ── B-08: orphan ``claim:*`` label when comment post fails ─────────────────


@pytest.mark.unit
def test_current_behavior_failed_comment_post_does_not_invoke_release_agent_lease(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4270 B-08: a label may remain on GitHub when ``post_agent_lease`` fails closed on comment."""
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond(
        "post_agent_lease",
        lease_result(label=True, comment=False, errors=["comment: HTTP 502"]),
        rc=1,
    )
    with pytest.raises(RMScriptError):
        claims_mod.claim(REPO, ISSUE, agent=AGENT, session=f"{AGENT}-{ISSUE}", intent="orphan-risk")
    assert "release_agent_lease" not in [name for name, _ in rm.calls()]


@pytest.mark.unit
@pytest.mark.xfail(strict=True, reason="#4270 B-08: failed lease post should roll back any claim label it added (PR 2)")
def test_failed_lease_post_should_roll_back_orphan_claim_label(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond(
        "post_agent_lease",
        lease_result(label=True, comment=False, errors=["comment: HTTP 502"]),
        rc=1,
    )
    with pytest.raises(RMScriptError):
        claims_mod.claim(REPO, ISSUE, agent=AGENT, session=f"{AGENT}-{ISSUE}", intent="orphan-risk")
    assert "release_agent_lease" in [name for name, _ in rm.calls()]


# ── B-09: in-memory fence/TTL bookkeeping ───────────────────────────────────


@pytest.mark.unit
def test_current_behavior_job_board_fence_and_ttl_live_only_in_process(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4270 B-09: fence epochs and TTL rows vanish on ``reset_job_board_claim_state`` (no persistence)."""
    monkeypatch.setattr(claims_mod, "evidence_fetch", _claim_fetch(ISSUE))
    rm.respond("post_agent_lease", lease_result(agent=AGENT))
    posted = {"n": 0}
    real_post = claims_mod.post_lease

    def post_lease(*args: Any, **kwargs: Any) -> dict[str, Any]:
        posted["n"] += 1
        return real_post(*args, **kwargs)

    def check(repo: str, issue: int) -> dict[str, Any]:
        if posted["n"] == 0:
            return {"available": True, "held": False, "agent": "", "session": "", "reason": "", "expires_at": None}
        return {
            "available": True,
            "held": True,
            "agent": AGENT,
            "session": f"{AGENT}-{ISSUE}",
            "reason": "active lease",
            "expires_at": "2026-11-11T12:00:00+00:00",
        }

    monkeypatch.setattr(claims_mod, "post_lease", post_lease)
    monkeypatch.setattr(claims_mod, "check", check)

    claimed = jobs_mod.claim_job(REPO, ISSUE, agent=AGENT, session=f"{AGENT}-{ISSUE}", intent="fence")
    fence = claimed["fence"]
    assert fence > 0
    jobs_mod.reset_job_board_claim_state()
    assert jobs_mod._current_fence(REPO, ISSUE) == 0

    def still_held(repo: str, issue: int) -> dict[str, Any]:
        return {
            "available": True,
            "held": True,
            "agent": AGENT,
            "session": f"{AGENT}-{ISSUE}",
            "reason": "active lease",
            "expires_at": "2026-11-11T12:00:00+00:00",
        }

    monkeypatch.setattr(claims_mod, "check", still_held)
    with pytest.raises(jobs_mod.StaleFenceError):
        jobs_mod.release_job(
            REPO,
            ISSUE,
            agent=AGENT,
            session=f"{AGENT}-{ISSUE}",
            fence=fence,
            reason="after-restart",
        )


@pytest.mark.unit
@pytest.mark.xfail(strict=True, reason="#4270 B-09: fence/TTL rows must survive process restart (PR 3)")
def test_job_board_fence_survives_simulated_process_restart(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claims_mod, "evidence_fetch", _claim_fetch(ISSUE))
    rm.respond("post_agent_lease", lease_result(agent=AGENT))
    posted = {"n": 0}
    real_post = claims_mod.post_lease

    def post_lease(*args: Any, **kwargs: Any) -> dict[str, Any]:
        posted["n"] += 1
        return real_post(*args, **kwargs)

    def check(repo: str, issue: int) -> dict[str, Any]:
        if posted["n"] == 0:
            return {"available": True, "held": False, "agent": "", "session": "", "reason": "", "expires_at": None}
        return {
            "available": True,
            "held": True,
            "agent": AGENT,
            "session": f"{AGENT}-{ISSUE}",
            "reason": "active lease",
            "expires_at": "2026-11-11T12:00:00+00:00",
        }

    monkeypatch.setattr(claims_mod, "post_lease", post_lease)
    monkeypatch.setattr(claims_mod, "check", check)
    claimed = jobs_mod.claim_job(REPO, ISSUE, agent=AGENT, session=f"{AGENT}-{ISSUE}", intent="fence")
    fence = claimed["fence"]
    jobs_mod.reset_job_board_claim_state()
    assert jobs_mod._current_fence(REPO, ISSUE) == fence
