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
from coordination import job_board_lease
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
    issue_fetch = f"/repos/D-sorganization/{REPO}/issues/{ISSUE}"
    evidence_paths: list[str] = []
    fetch_issue = _claim_fetch(ISSUE)

    def spy_evidence_fetch(path: str) -> Any:
        evidence_paths.append(path)
        return fetch_issue(path)

    monkeypatch.setattr(claims_mod, "evidence_fetch", spy_evidence_fetch)
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(agent=AGENT))
    rm_check_calls = {"n": 0}
    lease_posted = {"n": 0}
    real_check = claims_mod.check

    def check(repo: str, issue: int) -> dict[str, Any]:
        rm_check_calls["n"] += 1
        if lease_posted["n"] > 0:
            return {
                "available": True,
                "held": True,
                "agent": AGENT,
                "session": f"{AGENT}-{issue}",
                "reason": "active lease",
                "expires_at": "2026-11-11T12:00:00+00:00",
            }
        return real_check(repo, issue)

    ttl_bookkeeping: list[dict[str, Any]] = []
    real_remember = job_board_lease.remember_lease

    def remember_lease(*args: Any, **kwargs: Any) -> None:
        ttl_bookkeeping.append({"args": args, "kwargs": kwargs})
        real_remember(*args, **kwargs)

    real_post = claims_mod.post_lease

    def post_lease(*args: Any, **kwargs: Any) -> dict[str, Any]:
        lease_posted["n"] += 1
        return real_post(*args, **kwargs)

    monkeypatch.setattr(claims_mod, "check", check)
    monkeypatch.setattr(claims_mod, "post_lease", post_lease)
    monkeypatch.setattr(job_board_lease, "remember_lease", remember_lease)

    before = len(activity_store.read(session_id=f"{AGENT}-{ISSUE}").items)
    result = jobs_mod.claim_job(REPO, ISSUE, agent=AGENT, session=f"{AGENT}-{ISSUE}", intent="characterize")
    after = len(activity_store.read(session_id=f"{AGENT}-{ISSUE}").items)

    rm_names = [name for name, _ in rm.calls()]
    touched = {
        "rm_check": rm_check_calls["n"] >= 1 and "check_agent_claim" in rm_names,
        "rm_post": "post_agent_lease" in rm_names,
        "gh_evidence": issue_fetch in evidence_paths,
        "in_memory_fence": jobs_mod._current_fence(REPO, ISSUE) == result["fence"] > 0,
        "in_memory_ttl": len(ttl_bookkeeping) == 1
        and ttl_bookkeeping[0]["kwargs"].get("session") == f"{AGENT}-{ISSUE}",
        "activity_log": after > before,
    }

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
    """Simulates two workers that share RM but not this process's ``_issue_lock`` serialization."""
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(agent=AGENT))
    monkeypatch.setattr(claims_mod, "evidence_fetch", lambda _path: [])

    # Fresh lock per ``claim()`` call — no cross-thread serialization (multi-worker / multi-process).
    monkeypatch.setattr(claims_mod, "_issue_lock", lambda _repo, _issue: threading.Lock())

    store_guard = threading.Lock()
    durable_holder: dict[str, Any] = {"session": "", "posted": 0}
    real_check = claims_mod.check
    real_post = claims_mod._post

    def shared_check(repo: str, issue: int) -> dict[str, Any]:
        with store_guard:
            if durable_holder["posted"] > 0:
                return {
                    "available": True,
                    "held": True,
                    "agent": AGENT,
                    "session": durable_holder["session"],
                    "reason": "lease",
                    "expires_at": None,
                }
        return real_check(repo, issue)

    def shared_post(repo: str, issue: int, agent: str, session: str, intent: str) -> dict[str, Any]:
        result = real_post(repo, issue, agent, session, intent)
        with store_guard:
            durable_holder["posted"] += 1
            durable_holder["session"] = session
        return result

    monkeypatch.setattr(claims_mod, "check", shared_check)
    monkeypatch.setattr(claims_mod, "_post", shared_post)

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    outcomes_guard = threading.Lock()

    def racer(session: str) -> None:
        barrier.wait(5)
        try:
            claims_mod.claim(REPO, ISSUE, agent=AGENT, session=session, intent="race")
            with outcomes_guard:
                outcomes.append("ok")
        except claims_mod.ClaimHeldError:
            with outcomes_guard:
                outcomes.append("held")

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
