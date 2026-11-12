"""Dispatch admission applies a claim label before the worker lease ritual (#4063).

Regression: ``lease.acquire`` must not treat our own dispatch label as a foreign hold.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from activity import ActivityStore
from staff import claim_guard, dispatch_self_claim
from staff import lease as lease_mod
from staff.store import RunRecord, RunStore

from tests.api.coordination_fake_rm import FakeRM, claim_status, lease_result

RUN_ID = "run-selfclaim1"
REPO = "Runner_Dashboard"
ISSUE = "4063"
AGENT = "antigravity"


def _seed_run(store: RunStore, run_id: str = RUN_ID) -> None:
    store.create_run(
        RunRecord(
            id=run_id,
            role="issue-remediator",
            provider="antigravity",
            model=None,
            machine="test",
            repo=REPO,
            target_kind="issue",
            target_ref=f"#{ISSUE}",
            prompt="fix",
            branch="cursor/4063-fix",
        )
    )


@pytest.fixture
def rm_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRM:
    fake = FakeRM(tmp_path / "rm")
    monkeypatch.setenv("STAFF_RM_ROOT", str(fake.root))
    monkeypatch.setenv("STAFF_RM_PYTHON", sys.executable)
    claim_guard.reset_applied()
    yield fake
    claim_guard.reset_applied()


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


def test_acquire_continues_when_check_reports_dispatch_label_hold(
    rm_root: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing case before fix: held due to claim:staff from apply_claim aborts the worker."""
    claim_guard._applied[RUN_ID] = (f"D-sorganization/{REPO}", int(ISSUE), "claim:staff")
    rm_root.respond(
        "check_agent_claim",
        claim_status(
            True,
            "staff",
            f"issue carries claim:staff from staff dispatch (run {RUN_ID})",
        ),
    )
    rm_root.respond("post_agent_lease", lease_result(agent=AGENT))
    rm_root.respond("agent_communicate:register", {"ok": True})

    _seed_run(store)
    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="cursor/4063-fix")
    assert note is not None
    assert "lease and presence" in note.lower()
    rec = store.get_run(RUN_ID)
    assert rec is not None and rec.status != "blocked"
    names = [name for name, _ in rm_root.calls()]
    assert "post_agent_lease" in names


def test_acquire_still_blocks_foreign_agent_hold(
    rm_root: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim_guard._applied[RUN_ID] = (f"D-sorganization/{REPO}", int(ISSUE), "claim:staff")
    rm_root.respond(
        "check_agent_claim",
        {**claim_status(True, "codex", "active lease"), "session": "codex-other"},
    )

    _seed_run(store)
    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")
    assert note is None
    rec = store.get_run(RUN_ID)
    assert rec is not None and rec.failure_class == "lease_blocked"
    assert "post_agent_lease" not in [name for name, _ in rm_root.calls()]


def test_acquire_still_blocks_merged_pr_guard_even_with_dispatch_label(
    rm_root: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim_guard._applied[RUN_ID] = (f"D-sorganization/{REPO}", int(ISSUE), "claim:staff")
    rm_root.respond(
        "check_agent_claim",
        claim_status(True, None, "merged PR #999 already closes this issue"),
    )

    _seed_run(store)
    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")
    assert note is None
    assert store.get_run(RUN_ID).failure_class == "lease_blocked"


def test_acquire_block_records_fenced_diagnostics_for_later_claim_lookup(
    rm_root: FakeRM, store: RunStore, activity_store: ActivityStore
) -> None:
    """#4156: a dispatch-time block must leave fenced evidence a later claim lookup can see.

    The admission check (here) and the operator claim lookup (``coordination.claims.check``)
    each take an independent live GitHub read with no shared snapshot; this is the evidence
    trail that lets an operator explain a later "unheld" read instead of treating it as an
    unexplained contradiction.
    """
    held_session = "codex-other"
    rm_root.respond(
        "check_agent_claim",
        {**claim_status(True, "codex", "active lease"), "session": held_session, "expires_at": "2026-11-08T00:00:00Z"},
    )
    _seed_run(store)

    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")

    assert note is None
    assert store.get_run(RUN_ID).failure_class == "lease_blocked"
    session = dispatch_self_claim.expected_staff_session(RUN_ID)
    page = activity_store.read(session_id=session)
    conflicts = [item for item in page.items if item.kind == "conflict"]
    assert len(conflicts) == 1
    payload = conflicts[0].payload
    assert payload["repo"] == REPO
    assert payload["issue"] == int(ISSUE)
    assert payload["holder_principal"] == "agent-codex"
    assert payload["run_id"] == RUN_ID
    assert payload["decision"] == "lease_blocked"
    assert payload["held_expires_at"] == "2026-11-08T00:00:00Z"
    assert payload["storage_backend"] == "rm.check_agent_claim"


def test_acquire_block_sanitizes_path_and_token_like_reasons(
    rm_root: FakeRM, store: RunStore, activity_store: ActivityStore
) -> None:
    unsafe_reason = "blocked by /home/dieterolson/.secrets/creds and ghp_abcdEFGH12345678"
    rm_root.respond(
        "check_agent_claim",
        {**claim_status(True, "codex", unsafe_reason), "session": "codex-other"},
    )
    _seed_run(store)

    lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")

    session = dispatch_self_claim.expected_staff_session(RUN_ID)
    page = activity_store.read(session_id=session)
    conflicts = [item for item in page.items if item.kind == "conflict"]
    assert len(conflicts) == 1
    held_reason = conflicts[0].payload["held_reason"]
    assert "/home/dieterolson" not in held_reason
    assert "ghp_abcdEFGH12345678" not in held_reason
    assert "<redacted-path>" in held_reason
    assert "<redacted-token>" in held_reason


def test_acquire_block_diagnostics_skip_cleanly_on_non_numeric_issue(
    rm_root: FakeRM, store: RunStore, activity_store: ActivityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed issue id must not break the real block decision (#4156)."""
    rm_root.respond(
        "check_agent_claim",
        {**claim_status(True, "codex", "active lease"), "session": "codex-other"},
    )
    store.create_run(
        RunRecord(
            id=RUN_ID,
            role="issue-remediator",
            provider="antigravity",
            model=None,
            machine="test",
            repo=REPO,
            target_kind="issue",
            target_ref="#not-a-number",
            prompt="fix",
            branch="cursor/4063-fix",
        )
    )

    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue="not-a-number", agent=AGENT, branch="b")

    assert note is None
    assert store.get_run(RUN_ID).failure_class == "lease_blocked"
    session = dispatch_self_claim.expected_staff_session(RUN_ID)
    page = activity_store.read(session_id=session)
    assert [item.kind for item in page.items] == []


def test_acquire_continues_when_rm_reports_no_active_lease_for_own_label(
    rm_root: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4216 regression: RM's bare-label reason "(no active lease)" is not a foreign hold.

    ``check_agent_claim`` reports ``held=True`` with ``reason="claim label
    claim:issue-remediator (no active lease)"`` whenever a pre-applied dispatch
    label exists but nobody has posted a lease comment. The substring "active
    lease" previously matched ``_HOLD_BLOCKERS`` even though the full phrase
    says the opposite (*no* active lease), so the worker treated its own label
    as a foreign hold and blocked itself.
    """
    role_agent = "issue-remediator"
    claim_guard._applied[RUN_ID] = (f"D-sorganization/{REPO}", int(ISSUE), f"claim:{role_agent}")
    rm_root.respond(
        "check_agent_claim",
        claim_status(True, role_agent, f"claim label claim:{role_agent} (no active lease)"),
    )
    rm_root.respond("post_agent_lease", lease_result(agent=role_agent))
    rm_root.respond("agent_communicate:register", {"ok": True})

    _seed_run(store)
    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=role_agent, branch="cursor/4063-fix")
    assert note is not None
    rec = store.get_run(RUN_ID)
    assert rec is not None and rec.status != "blocked" and rec.failure_class != "lease_blocked"
    assert "post_agent_lease" in [name for name, _ in rm_root.calls()]


def test_is_own_dispatch_claim_hold_unit() -> None:
    check: dict[str, Any] = {
        "held": True,
        "agent": "staff",
        "reason": "claim:issue-remediator present",
    }
    claim_guard._applied["run-x"] = ("D-sorganization/Runner_Dashboard", 1, "claim:issue-remediator")
    assert dispatch_self_claim.is_own_dispatch_claim_hold("run-x", check, agent="antigravity", session="staff-run-x")
    foreign = {**claim_status(True, "codex", "active lease"), "session": "codex-1"}
    assert not dispatch_self_claim.is_own_dispatch_claim_hold(
        "run-x", foreign, agent="antigravity", session="staff-run-x"
    )


def test_is_own_dispatch_claim_hold_not_confused_by_no_active_lease_negation() -> None:
    """#4216: "(no active lease)" must not match the "active lease" blocker substring."""
    claim_guard._applied["run-y"] = ("D-sorganization/Runner_Dashboard", 2, "claim:issue-remediator")
    own_bare_label: dict[str, Any] = {
        "held": True,
        "agent": "issue-remediator",
        "reason": "claim label claim:issue-remediator (no active lease)",
    }
    assert dispatch_self_claim.is_own_dispatch_claim_hold(
        "run-y", own_bare_label, agent="antigravity", session="staff-run-y"
    )
