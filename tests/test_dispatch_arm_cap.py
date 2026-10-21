"""Contract tests for armed-PR WIP cap (RD-CI-005, issue #2029)."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import dispatch_arm_cap as arm_cap  # noqa: E402

_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "automerge_guard.py"
_SPEC = importlib.util.spec_from_file_location("automerge_guard", _MODULE_PATH)
automerge_guard = importlib.util.module_from_spec(_SPEC)
sys.modules["automerge_guard"] = automerge_guard
_SPEC.loader.exec_module(automerge_guard)


def _policy(tmp_path: Path, **overrides: object) -> arm_cap.ArmCapPolicy:
    data = {
        "enabled": True,
        "shadow_mode": False,
        "max_healthy_armed": 2,
        "reservation_ttl_seconds": 60,
        "reconcile_interval_seconds": 60,
        "scope_repositories": None,
        "required_checks_path": "config/ci-required-checks-live.json",
        "fallback_checks_path": "config/required_status_checks_policy.json",
    }
    data.update(overrides)
    cfg = tmp_path / "dispatch_limits.json"
    cfg.write_text(json.dumps(data), encoding="utf-8")
    return arm_cap.ArmCapPolicy.from_dict(data, tmp_path)


def _snap(
    repo: str,
    pr: int,
    *,
    armed: bool = True,
    mergeable_state: str = "clean",
    labels: tuple[str, ...] = (),
    checks: tuple[tuple[str, str], ...] = (),
    in_mq: bool = False,
) -> arm_cap.PrArmSnapshot:
    return arm_cap.PrArmSnapshot(
        repo=repo,
        number=pr,
        armed=armed,
        draft=False,
        mergeable_state=mergeable_state,
        labels=labels,
        in_merge_queue=in_mq,
        merge_queue_state="queued" if in_mq else "",
        check_runs=checks,
    )


@pytest.mark.unit
def test_classify_excludes_conflict_disarmed_and_dirty() -> None:
    bad = _snap("o/r", 1, labels=(arm_cap.CONFLICT_DISARMED_LABEL,))
    assert arm_cap.classify_arm_health(bad, ("tests",)).healthy is False
    dirty = _snap("o/r", 2, mergeable_state="dirty")
    assert arm_cap.classify_arm_health(dirty, ()).lifecycle == "evicted"


@pytest.mark.unit
def test_classify_required_check_failure(tmp_path: Path) -> None:
    fallback = tmp_path / "checks.json"
    fallback.write_text(json.dumps({"required_contexts": [{"context": "tests"}]}), encoding="utf-8")
    policy = _policy(tmp_path, fallback_checks_path=str(fallback.name))
    policy = arm_cap.ArmCapPolicy.from_dict(
        {
            "enabled": True,
            "shadow_mode": False,
            "max_healthy_armed": 2,
            "fallback_checks_path": str(fallback),
            "required_checks_path": str(tmp_path / "missing-live.json"),
        },
        tmp_path,
    )
    contexts = arm_cap.load_required_contexts(policy)
    assert contexts == ("tests",)
    snap = _snap("o/r", 3, checks=(("tests", "failure"),))
    verdict = arm_cap.classify_arm_health(snap, contexts)
    assert verdict.healthy is False


@pytest.mark.unit
def test_concurrent_admit_respects_cap(tmp_path: Path) -> None:
    policy = _policy(tmp_path, max_healthy_armed=1)
    db = tmp_path / "cap.sqlite3"
    store = arm_cap.ArmCapStore(db)
    ctx = arm_cap.AdmitContext()
    results: list[bool] = []
    barrier = threading.Barrier(2)

    def attempt(pr: int) -> None:
        barrier.wait()
        decision = arm_cap.admit_for_arm("o/r", pr, ctx, store=store, policy=policy)
        results.append(decision.allowed and decision.reservation_token is not None)

    t1 = threading.Thread(target=attempt, args=(20,))
    t2 = threading.Thread(target=attempt, args=(21,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sum(results) == 1


@pytest.mark.unit
def test_cap_boundary_blocks_third_arm(tmp_path: Path) -> None:
    policy = _policy(tmp_path, max_healthy_armed=2)
    store = arm_cap.ArmCapStore(tmp_path / "cap.sqlite3")
    arm_cap.reconcile_snapshots(
        [_snap("o/r", 1), _snap("o/r", 2)],
        store=store,
        policy=policy,
    )
    blocked = arm_cap.admit_for_arm("o/r", 3, arm_cap.AdmitContext(), store=store, policy=policy)
    assert blocked.allowed is False


@pytest.mark.unit
def test_stale_reservation_expires_and_frees_slot(tmp_path: Path) -> None:
    policy = _policy(tmp_path, max_healthy_armed=1)
    store = arm_cap.ArmCapStore(tmp_path / "cap.sqlite3")
    past = "2000-01-01T00:00:00Z"
    decision = store.try_reserve("o/r", 9, arm_cap.AdmitContext(), cap=1, ttl_seconds=1, now_iso=past)
    assert decision.allowed
    store.expire_stale_reservations(now_iso="2026-01-01T00:00:00Z")
    again = arm_cap.admit_for_arm("o/r", 10, arm_cap.AdmitContext(), store=store, policy=policy)
    assert again.allowed


@pytest.mark.unit
def test_shadow_mode_logs_but_allows_at_cap(tmp_path: Path) -> None:
    policy = _policy(tmp_path, max_healthy_armed=1, shadow_mode=True)
    store = arm_cap.ArmCapStore(tmp_path / "cap.sqlite3")
    arm_cap.reconcile_snapshots([_snap("o/r", 1)], store=store, policy=policy)
    decision = arm_cap.admit_for_arm("o/r", 2, arm_cap.AdmitContext(), store=store, policy=policy)
    assert decision.allowed
    assert decision.shadow_would_block


@pytest.mark.unit
def test_user_and_repair_bypass(tmp_path: Path) -> None:
    policy = _policy(tmp_path, max_healthy_armed=0)
    store = arm_cap.ArmCapStore(tmp_path / "cap.sqlite3")
    user = arm_cap.admit_for_arm("o/r", 1, arm_cap.AdmitContext(tier="user"), store=store, policy=policy)
    repair = arm_cap.admit_for_arm(
        "o/r",
        2,
        arm_cap.AdmitContext(tier="repair", dispatch_kind="ci_fix:lint"),
        store=store,
        policy=policy,
    )
    blocked_repair = arm_cap.admit_for_arm(
        "o/r",
        3,
        arm_cap.AdmitContext(tier="repair", dispatch_kind="implementation"),
        store=store,
        policy=policy,
    )
    assert user.allowed and repair.allowed
    assert not blocked_repair.allowed


@pytest.mark.unit
def test_restart_recovery_counts_pending_reservation(tmp_path: Path) -> None:
    store = arm_cap.ArmCapStore(tmp_path / "cap.sqlite3")
    store.try_reserve("o/r", 5, arm_cap.AdmitContext(), cap=10, ttl_seconds=3600)
    assert store.recover_pending_as_inventory_gap() == 1


@pytest.mark.unit
def test_reconcile_backlog_updates_inventory(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    store = arm_cap.ArmCapStore(tmp_path / "cap.sqlite3")
    counts = arm_cap.reconcile_snapshots(
        [_snap("o/r", 1), _snap("o/r", 2, armed=False)],
        store=store,
        policy=policy,
    )
    assert counts["healthy"] == 1
    assert store.count_healthy_armed() == 1


@pytest.mark.unit
def test_disarm_requires_streak_and_cooldown(tmp_path: Path) -> None:
    policy = _policy(tmp_path, max_healthy_armed=1, shadow_mode=False, disarm_transient_streak_limit=2)
    store = arm_cap.ArmCapStore(tmp_path / "cap.sqlite3")
    assert not arm_cap.should_trigger_cap_disarm(3, policy, store)
    assert not arm_cap.should_trigger_cap_disarm(3, policy, store)
    assert arm_cap.should_trigger_cap_disarm(3, policy, store)


@pytest.mark.unit
def test_automerge_guard_refuses_when_cap_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, max_healthy_armed=0, shadow_mode=False)
    monkeypatch.setattr(arm_cap, "load_policy", lambda _root=None, policy_path=None: policy)
    monkeypatch.setattr(arm_cap, "DEFAULT_STORE_PATH", tmp_path / "cap.sqlite3")
    monkeypatch.setattr(automerge_guard, "_import_arm_cap_module", lambda: arm_cap)
    monkeypatch.setattr(
        automerge_guard,
        "arm_auto_merge",
        lambda *a, **k: automerge_guard.ArmResult(True, automerge_guard.HoldVerdict(False), "armed"),
    )
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **k: True)
    code = automerge_guard.main(
        ["o/r", "7", "--arm", "--repo-root", str(tmp_path), "--arm-tier", "agent"]
    )
    assert code == automerge_guard.EXIT_ARM_CAP_REFUSED
    assert "cap" in capsys.readouterr().out.lower()


@pytest.mark.unit
def test_automerge_guard_confirms_reservation_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _policy(tmp_path, max_healthy_armed=5, shadow_mode=False)
    store = arm_cap.ArmCapStore(tmp_path / "cap.sqlite3")
    monkeypatch.setattr(arm_cap, "load_policy", lambda _root=None, policy_path=None: policy)
    monkeypatch.setattr(arm_cap, "DEFAULT_STORE_PATH", tmp_path / "cap.sqlite3")
    monkeypatch.setattr(automerge_guard, "_import_arm_cap_module", lambda: arm_cap)
    monkeypatch.setattr(
        automerge_guard,
        "arm_auto_merge",
        lambda *a, **k: automerge_guard.ArmResult(True, automerge_guard.HoldVerdict(False), "armed"),
    )
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **k: True)
    assert automerge_guard.main(["o/r", "7", "--arm", "--repo-root", str(tmp_path)]) == 0
    with store._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT status FROM arm_reservations WHERE repo = 'o/r' AND pr = 7"
        ).fetchone()
    assert row and row["status"] == "confirmed"
