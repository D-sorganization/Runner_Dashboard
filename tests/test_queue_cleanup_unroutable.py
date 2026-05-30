"""Tests for unroutable-label detection in backend/queue_cleanup.py.

A queued job runs only on a runner whose label set is a superset of the job's
`runs-on` labels. When a workflow requests a label that no *online* runner
advertises (a removed/renamed runner tier), the run sits queued forever. The
reaper now detects these and marks them `unroutable-label`, safe to cancel.
"""

from __future__ import annotations

import asyncio
import datetime as _dt

import queue_cleanup as qc


def _iso_ago(minutes: int) -> str:
    return (qc._get_now() - _dt.timedelta(minutes=minutes)).isoformat()


def _make_gh_json(queued_runs, jobs_by_run):
    async def fake(*args, **_kwargs):
        path = args[1] if len(args) > 1 else ""
        if "status=queued" in path:
            return {"workflow_runs": queued_runs}
        if "status=in_progress" in path:
            return {"workflow_runs": []}
        if "/jobs" in path:
            run_id = int(path.split("/actions/runs/")[1].split("/jobs")[0])
            return {"jobs": jobs_by_run.get(run_id, [])}
        return {}

    return fake


# ---------------------------------------------------------------------------
# is_routable (pure)
# ---------------------------------------------------------------------------


def test_is_routable_true_when_a_runner_superset_matches() -> None:
    assert qc.is_routable(["d-sorg-fleet"], [frozenset({"self-hosted", "d-sorg-fleet"})]) is True


def test_is_routable_requires_all_labels_on_one_runner() -> None:
    # Job needs BOTH labels; the runner has only one -> not routable.
    online = [frozenset({"self-hosted"}), frozenset({"d-sorg-fleet"})]
    assert qc.is_routable(["self-hosted", "d-sorg-fleet"], online) is False


def test_is_routable_false_when_label_on_no_runner() -> None:
    assert qc.is_routable(["d-sorg-fleet-16core"], [frozenset({"d-sorg-fleet"})]) is False


def test_is_routable_empty_required_is_true() -> None:
    # Missing job metadata must never be treated as stuck.
    assert qc.is_routable([], [frozenset()]) is True


def test_is_routable_false_when_no_online_runners() -> None:
    assert qc.is_routable(["d-sorg-fleet"], []) is False


# ---------------------------------------------------------------------------
# fetch_online_runner_label_sets / required_labels_for_run
# ---------------------------------------------------------------------------


def test_fetch_online_runner_label_sets_filters_offline(monkeypatch) -> None:
    async def fake(*_args, **_kwargs):
        return {
            "runners": [
                {"status": "online", "labels": [{"name": "self-hosted"}, {"name": "d-sorg-fleet"}]},
                {"status": "offline", "labels": [{"name": "d-sorg-fleet-14core"}]},
            ]
        }

    monkeypatch.setattr(qc, "_gh_json", fake)
    sets = asyncio.run(qc.fetch_online_runner_label_sets("org"))
    assert sets == [frozenset({"self-hosted", "d-sorg-fleet"})]


def test_fetch_online_runner_label_sets_failure_returns_empty(monkeypatch) -> None:
    async def fake(*_args, **_kwargs):
        return None

    monkeypatch.setattr(qc, "_gh_json", fake)
    assert asyncio.run(qc.fetch_online_runner_label_sets("org")) == []


def test_required_labels_prefers_queued_job(monkeypatch) -> None:
    async def fake(*_args, **_kwargs):
        return {
            "jobs": [
                {"status": "completed", "labels": ["d-sorg-fleet-docker"]},
                {"status": "queued", "labels": ["d-sorg-fleet-8core"]},
            ]
        }

    monkeypatch.setattr(qc, "_gh_json", fake)
    assert asyncio.run(qc.required_labels_for_run("org", "repo", 1)) == ["d-sorg-fleet-8core"]


# ---------------------------------------------------------------------------
# _queued_stale_for_repo integration
# ---------------------------------------------------------------------------


def test_dead_label_queued_run_flagged_unroutable(monkeypatch) -> None:
    runs = [
        {
            "id": 111,
            "name": "CI",
            "head_branch": "feature/x",
            "status": "queued",
            "created_at": _iso_ago(120),
            "event": "schedule",
        }
    ]
    jobs = {111: [{"status": "queued", "labels": ["d-sorg-fleet-16core"]}]}
    monkeypatch.setattr(qc, "_gh_json", _make_gh_json(runs, jobs))

    out = asyncio.run(
        qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60), [frozenset({"d-sorg-fleet"})])
    )
    unroutable = [r for r in out if r.reason == qc.StaleReason.UNROUTABLE_LABEL.value]
    assert len(unroutable) == 1
    assert unroutable[0].run_id == 111
    assert unroutable[0].safe_to_cancel is True
    assert "d-sorg-fleet-16core" in unroutable[0].reason_detail
    # The run must not be double-classified under a branch/age reason.
    assert sum(1 for r in out if r.run_id == 111) == 1


def test_routable_queued_run_not_flagged_unroutable(monkeypatch) -> None:
    runs = [
        {
            "id": 222,
            "name": "CI",
            "head_branch": "feature/y",
            "status": "queued",
            "created_at": _iso_ago(120),
            "event": "schedule",
        }
    ]
    jobs = {222: [{"status": "queued", "labels": ["d-sorg-fleet"]}]}
    monkeypatch.setattr(qc, "_gh_json", _make_gh_json(runs, jobs))

    out = asyncio.run(
        qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60), [frozenset({"d-sorg-fleet"})])
    )
    assert all(r.reason != qc.StaleReason.UNROUTABLE_LABEL.value for r in out)


def test_empty_inventory_never_flags_unroutable(monkeypatch) -> None:
    # When the online-runner inventory is unavailable, a transient API failure
    # must not cause false-positive cancellations.
    runs = [
        {
            "id": 333,
            "name": "CI",
            "head_branch": "feature/z",
            "status": "queued",
            "created_at": _iso_ago(120),
            "event": "schedule",
        }
    ]
    jobs = {333: [{"status": "queued", "labels": ["d-sorg-fleet-16core"]}]}
    monkeypatch.setattr(qc, "_gh_json", _make_gh_json(runs, jobs))

    out = asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60), []))
    assert all(r.reason != qc.StaleReason.UNROUTABLE_LABEL.value for r in out)


def test_young_unroutable_run_below_age_gate_is_ignored(monkeypatch) -> None:
    runs = [
        {
            "id": 444,
            "name": "CI",
            "head_branch": "feature/w",
            "status": "queued",
            "created_at": _iso_ago(5),
            "event": "schedule",
        }
    ]
    jobs = {444: [{"status": "queued", "labels": ["d-sorg-fleet-16core"]}]}
    monkeypatch.setattr(qc, "_gh_json", _make_gh_json(runs, jobs))

    out = asyncio.run(
        qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60), [frozenset({"d-sorg-fleet"})])
    )
    assert out == []
