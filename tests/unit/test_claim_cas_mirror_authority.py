"""Serving-hub SQLite CAS stays authoritative when the GitHub mirror fails (#4270).

``claims.claim`` (``acquire_compare_and_set`` at claims.py) is the hub write.
``scripts.check_agent_claim``, ``scripts.post_agent_lease`` and
``scripts.release_agent_lease`` are the GitHub mirror, not the winner.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import activity_producers
import pytest
from activity import ActivityStore
from coordination import claim_state as claim_state_mod
from coordination import claims as claims_mod
from coordination.board import RMScriptError

from tests.api.coordination_fake_rm import FakeRM, claim_status, install, lease_result, release_result

REPO = "Tools"
ISSUE = 2539
AGENT = "codex"
WINNER = f"{AGENT}-winner"
LOSER = f"{AGENT}-loser"
_DOC = Path(__file__).resolve().parents[2] / "docs" / "coordination" / "claim-authority-and-board-rollover.md"


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    generator = install(tmp_path, monkeypatch, {}, repos=REPO)
    fake = next(generator)
    claim_state_mod.reset_claim_state_store()
    try:
        yield fake
    finally:
        claim_state_mod.reset_claim_state_store()
        try:
            next(generator)
        except StopIteration:
            pass


@pytest.fixture
def activity_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ActivityStore:
    store = ActivityStore(tmp_path / "activity.sqlite3")
    monkeypatch.setattr(activity_producers, "get_activity_store", lambda: store)
    activity_producers._next_seq.clear()
    return store


def _held_by(session: str) -> None:
    row = claim_state_mod.get_store().read(REPO, ISSUE)
    assert row is not None
    assert row.state == claim_state_mod.ClaimState.HELD
    assert (row.agent, row.session) == (AGENT, session)


@pytest.mark.unit
def test_mirror_failure_keeps_one_sqlite_winner(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch, activity_store: ActivityStore
) -> None:
    """One CAS winner remains held when ``post_agent_lease`` fails; the other session loses."""
    del activity_store
    monkeypatch.setattr(claims_mod, "evidence_fetch", lambda _path: [])
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(label=False, comment=False, errors=["HTTP 502"]), rc=1)
    outcomes: list[str] = []
    guard = threading.Lock()

    def take(session: str) -> None:
        try:
            claims_mod.claim(REPO, ISSUE, agent=AGENT, session=session, intent="mirror-down")
            kind = "ok"
        except claims_mod.ClaimHeldError:
            kind = "held"
        except RMScriptError:
            kind = "mirror"
        with guard:
            outcomes.append(kind)

    threads = [threading.Thread(target=take, args=(session,)) for session in (WINNER, LOSER)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(outcomes) == ["held", "mirror"]
    row = claim_state_mod.get_store().read(REPO, ISSUE)
    assert row is not None and row.state == claim_state_mod.ClaimState.HELD
    assert row.session in {WINNER, LOSER}


@pytest.mark.unit
def test_orphan_label_rollback_does_not_release_the_sqlite_claim(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch, activity_store: ActivityStore
) -> None:
    """GitHub label cleanup may run. It must not abandon the CAS row."""
    del activity_store
    monkeypatch.setattr(claims_mod, "evidence_fetch", lambda _path: [])
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond(
        "post_agent_lease",
        lease_result(label=True, comment=False, errors=["comment: HTTP 502"]),
        rc=1,
    )
    rm.respond("release_agent_lease", release_result())
    with pytest.raises(RMScriptError):
        claims_mod.claim(REPO, ISSUE, agent=AGENT, session=WINNER, intent="orphan-label")
    assert "release_agent_lease" in [name for name, _argv in rm.calls()]
    _held_by(WINNER)


@pytest.mark.unit
def test_same_holder_retries_the_mirror_other_session_still_loses(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch, activity_store: ActivityStore
) -> None:
    """Failover: the CAS holder retries the mirror. A different session does not take the row."""
    del activity_store
    monkeypatch.setattr(claims_mod, "evidence_fetch", lambda _path: [])
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(label=False, comment=False, errors=["HTTP 502"]), rc=1)
    with pytest.raises(RMScriptError):
        claims_mod.claim(REPO, ISSUE, agent=AGENT, session=WINNER, intent="first")
    with pytest.raises(claims_mod.ClaimHeldError):
        claims_mod.claim(REPO, ISSUE, agent=AGENT, session=LOSER, intent="steal")
    rm.respond("post_agent_lease", lease_result(agent=AGENT))
    renewed = claims_mod.claim(REPO, ISSUE, agent=AGENT, session=WINNER, intent="retry")
    assert renewed["claimed"] is True
    _held_by(WINNER)


@pytest.mark.unit
def test_cas_insert_failure_rolls_back_and_leaves_no_holder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception inside the CAS transaction rolls back. No holder is left behind."""
    monkeypatch.setenv("CLAIM_STATE_DB", str(tmp_path / "claim_state.sqlite3"))
    claim_state_mod.reset_claim_state_store()
    store = claim_state_mod.get_store()
    original = claim_state_mod.is_legal_transition

    def reject(*_args: object, **_kwargs: object) -> bool:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(claim_state_mod, "is_legal_transition", reject)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        store.try_acquire(REPO, ISSUE, agent=AGENT, session=WINNER)
    monkeypatch.setattr(claim_state_mod, "is_legal_transition", original)
    assert store.read(REPO, ISSUE) is None
    claim_state_mod.reset_claim_state_store()


@pytest.mark.unit
def test_rm_2539_rollover_keeps_history_and_never_deletes_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At GitHub's 2500-comment cap, rollover moves the pointer and does not delete #2539."""
    from coordination import board_rollover as br_mod

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg = {
        "version": 1,
        "board_repo": "D-sorganization/Repository_Management",
        "board_issue": 2539,
        "board_predecessors": [100, 200, 2376],
    }
    (config_dir / "agent_communication.json").write_text(json.dumps(cfg), encoding="utf-8")
    calls: list[list[str]] = []

    def runner(cmd: list[str]) -> MagicMock:
        calls.append(list(cmd))
        text = " ".join(cmd)
        if "issues/2539" in text:
            return MagicMock(returncode=0, stdout=json.dumps({"comments": 2500}), stderr="")
        if "issue" in text and "create" in text:
            url = "https://github.com/D-sorganization/Repository_Management/issues/3100\n"
            return MagicMock(returncode=0, stdout=url, stderr="")
        return MagicMock(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("coordination.board.reset_cache", MagicMock())
    result = br_mod.execute_board_rollover(
        tmp_path, threshold=500, runner=runner, journal_path=tmp_path / "journal.json"
    )
    saved = json.loads((config_dir / "agent_communication.json").read_text(encoding="utf-8"))
    assert result.rolled_over is True
    assert result.old_issue == 2539
    assert result.new_issue == 3100
    assert saved["board_issue"] == 3100
    assert 2539 in saved["board_predecessors"]
    assert 100 not in saved["board_predecessors"]
    blob = " ".join(" ".join(cmd) for cmd in calls).lower()
    assert "delete" not in blob


@pytest.mark.unit
def test_authority_map_names_cli_mirror_and_forbids_deleting_2539_history() -> None:
    text = _DOC.read_text(encoding="utf-8")
    for entry in ("scripts.check_agent_claim", "scripts.post_agent_lease", "scripts.release_agent_lease"):
        assert entry in text
    assert "claims.claim" in text
    assert "jobs.claim_job" in text
    assert "2539" in text
    assert "2500" in text
    assert "do not delete" in text.lower()
