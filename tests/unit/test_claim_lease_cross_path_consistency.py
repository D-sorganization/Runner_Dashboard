"""Cross-path claim/lease consistency reproductions (#4156).

``staff.lease.acquire`` (dispatch admission) and ``coordination.claims.check``/
``claims.claim`` (the operator claim lookup) each take their own independent,
uncached live ``check_agent_claim`` read — see ``staff.lease._record_block_diagnostics``'s
docstring. #4156 reported a run that was denied by the admission path, then a
later lookup reported the same issue unheld, and asked for deterministic tests
reproducing the scenarios named in its acceptance criteria rather than treating
every such pair of reads as a bug.

Covered here (same two call sites, same ``FakeRM`` canned responses, no
production code changed):

* claim-free admission — both paths agree the issue is free.
* an active conflicting claim — both paths agree it is blocked.
* same-owner renewal — and a genuine asymmetry this surfaced: the two paths
  define "my own hold" differently (``claims.claim`` by agent+session match
  alone; ``lease.acquire`` only by its own pre-applied ``claim:<role>`` label).
* the expire/release race between an admission block and a later lookup —
  this is #4156's reported scenario, reproduced deterministically by flipping
  the fake ``check_agent_claim`` response between the two calls.
* a stale coordination-board cache read diverging from a live, agreeing
  ``check_agent_claim`` read (the one real cached read in ``claims.check``).

Two scenarios named in #4156 already have dedicated coverage elsewhere, so are
not duplicated: fenced/stale-token takeover refusal is
``tests/api/test_coordination_jobs_release_fence.py``, and multi-process/
concurrent-claim-one-wins is ``tests/api/test_coordination_jobs.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from activity import ActivityStore
from coordination import board as board_mod
from coordination import claims as claims_mod
from staff import claim_guard
from staff import lease as lease_mod
from staff.dispatch_self_claim import expected_staff_session as dispatch_self_claim_expected_session
from staff.store import RunRecord, RunStore

from tests.api.coordination_fake_rm import FakeRM, claim_status, lease_result, session

RUN_ID = "run-crosspath1"
REPO = "Runner_Dashboard"
ISSUE = "4156"
AGENT = "antigravity"
OWN_SESSION = "antigravity-run-crosspath1"


def _seed_run(store: RunStore) -> None:
    store.create_run(
        RunRecord(
            id=RUN_ID,
            role="issue-remediator",
            provider="antigravity",
            model=None,
            machine="test",
            repo=REPO,
            target_kind="issue",
            target_ref=f"#{ISSUE}",
            prompt="fix",
            branch="cursor/4156-fix",
        )
    )


@pytest.fixture
def rm_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRM:
    fake = FakeRM(tmp_path / "rm")
    monkeypatch.setenv("STAFF_RM_ROOT", str(fake.root))
    monkeypatch.setenv("STAFF_RM_PYTHON", sys.executable)
    monkeypatch.setattr(claims_mod, "evidence_fetch", lambda _path: [])  # no live gh in a unit test (#3274)
    claim_guard.reset_applied()
    claims_mod.reset_locks()
    board_mod.reset_cache()
    yield fake
    claim_guard.reset_applied()
    claims_mod.reset_locks()
    board_mod.reset_cache()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunStore:
    path = tmp_path / "runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(path))
    return RunStore(path)


@pytest.fixture
def activity_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ActivityStore:
    import activity_producers

    real_store = ActivityStore(tmp_path / "activity.sqlite3")
    monkeypatch.setattr(activity_producers, "get_activity_store", lambda: real_store)
    activity_producers._next_seq.clear()
    return real_store


def test_claim_free_admission_agrees_across_both_paths(rm_root: FakeRM, store: RunStore) -> None:
    rm_root.respond("check_agent_claim", claim_status(False))
    rm_root.respond("post_agent_lease", lease_result(agent=AGENT))
    rm_root.respond("agent_communicate:register", {"ok": True})
    _seed_run(store)

    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")
    assert note is not None
    assert store.get_run(RUN_ID).failure_class != "lease_blocked"

    looked_up = claims_mod.check(REPO, int(ISSUE))
    assert looked_up == {"available": True, "held": False, "agent": "", "session": "", "reason": "", "expires_at": None}


def test_active_conflicting_claim_denied_on_both_paths(rm_root: FakeRM, store: RunStore) -> None:
    rm_root.respond(
        "check_agent_claim",
        {**claim_status(True, "codex", "active lease"), "session": "codex-other"},
    )
    _seed_run(store)

    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")
    assert note is None
    assert store.get_run(RUN_ID).failure_class == "lease_blocked"

    with pytest.raises(claims_mod.ClaimHeldError) as excinfo:
        claims_mod.claim(REPO, int(ISSUE), agent=AGENT, session=OWN_SESSION, intent="take it")
    assert excinfo.value.status["agent"] == "codex"


def test_same_agent_session_hold_is_not_symmetric_across_paths(rm_root: FakeRM, store: RunStore) -> None:
    """The two paths define "my own hold" differently — not a bug, but worth characterizing (#4156 scope item).

    ``claims.claim``'s ``_is_own`` treats any matching ``(agent, session)`` pair as a
    renewal. ``lease.acquire``'s exemption (``is_own_dispatch_claim_hold``) is narrower:
    it only recognizes a hold created by dispatch's own pre-applied ``claim:<role>``
    label (``claim_guard``), not a general "this agent+session already holds it" fact.
    A run whose own RM lease was posted some other way (e.g. by the operator claim API)
    still blocks dispatch admission for the identical agent+session.
    """
    rm_root.respond(
        "check_agent_claim",
        {**claim_status(True, AGENT, "renewing own lease"), "session": OWN_SESSION},
    )
    rm_root.respond("post_agent_lease", lease_result(agent=AGENT))
    _seed_run(store)

    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")
    assert note is None  # blocks: lease.acquire does not know this is "our own" lease
    assert store.get_run(RUN_ID).failure_class == "lease_blocked"

    # The operator claim-lookup path, by contrast, does treat matching agent+session as a renewal.
    result = claims_mod.claim(REPO, int(ISSUE), agent=AGENT, session=OWN_SESSION, intent="renew")
    assert result["claimed"] is True


def test_dispatch_pre_applied_label_hold_is_recognized_as_own_by_lease_acquire(
    rm_root: FakeRM, store: RunStore
) -> None:
    """The actual "own hold" lease.acquire does recognize: its own claim_guard-applied label."""
    claim_guard._applied[RUN_ID] = (f"D-sorganization/{REPO}", int(ISSUE), f"claim:{AGENT}")
    own_session = dispatch_self_claim_expected_session(RUN_ID)
    rm_root.respond(
        "check_agent_claim",
        {**claim_status(True, AGENT, f"issue carries claim:{AGENT} from staff dispatch"), "session": own_session},
    )
    rm_root.respond("post_agent_lease", lease_result(agent=AGENT))
    rm_root.respond("agent_communicate:register", {"ok": True})
    _seed_run(store)

    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")
    assert note is not None
    assert store.get_run(RUN_ID).failure_class != "lease_blocked"


def test_expire_release_race_between_admission_block_and_later_lookup(
    rm_root: FakeRM, store: RunStore, activity_store: ActivityStore
) -> None:
    """#4156's reported contradiction, reproduced deterministically.

    The admission path reads "held" and blocks the run; before any operator
    looks, the real holder legitimately releases or expires. A later,
    independent lookup now honestly reads "unheld" — not a bug, but exactly
    the TOCTOU the issue asked to characterize. The block's own diagnostics
    event is the evidence that reconciles the two reads.
    """
    rm_root.respond(
        "check_agent_claim",
        {
            **claim_status(True, "codex", "active lease"),
            "session": "codex-other",
            "expires_at": "2026-11-08T00:00:00Z",
        },
    )
    _seed_run(store)

    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")
    assert note is None
    assert store.get_run(RUN_ID).failure_class == "lease_blocked"

    # The holder releases (or the lease expires) before anyone looks again.
    rm_root.respond("check_agent_claim", claim_status(False))

    looked_up = claims_mod.check(REPO, int(ISSUE))
    assert looked_up["held"] is False

    from staff import dispatch_self_claim

    session_id = dispatch_self_claim.expected_staff_session(RUN_ID)
    page = activity_store.read(session_id=session_id)
    conflicts = [item for item in page.items if item.kind == "conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].payload["decision"] == "lease_blocked"
    assert conflicts[0].payload["held_expires_at"] == "2026-11-08T00:00:00Z"


def test_stale_board_cache_can_diverge_from_a_live_check_agent_claim_read(rm_root: FakeRM) -> None:
    """The one real cached read in ``claims.check`` (``_board_claim_override``).

    A live ``check_agent_claim`` read says free, but a stale coordination-board
    presence cache still lists an active session on the same issue; the cache
    wins so the lookup fails closed to "held" rather than reporting free on
    stale data. This is the inverse direction of #4156's reported case and the
    one place ``claims.check`` itself mixes a cached read with a live one.
    """
    rm_root.respond("check_agent_claim", claim_status(False))
    stale = session("stale-holder-session", REPO, issue=int(ISSUE), agent="codex")
    board_mod._cache["all"] = (
        board_mod._clock(),
        {"available": True, "complete": True, "sessions": [stale], "warnings": []},
    )

    result = claims_mod.check(REPO, int(ISSUE))

    assert result["held"] is True
    assert result["agent"] == "codex"
    assert "RM #" in result["reason"]
