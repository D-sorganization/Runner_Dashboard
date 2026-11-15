"""#4249 H-14 / B-22: the arm path times out, fails closed and says why.

Hermetic: no network, no real ``gh``; the cap store lives under ``tmp_path``.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.test_automerge_guard import FakeGh, _completed, automerge_guard

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import dispatch_arm_cap as arm_cap  # noqa: E402

TIMED_OUT = automerge_guard.EXIT_GH_TIMED_OUT


def _policy(tmp_path: Path, **overrides: object) -> arm_cap.ArmCapPolicy:
    data: dict[str, object] = {"enabled": True, "shadow_mode": False, "max_healthy_armed": 5}
    data.update(overrides)
    return arm_cap.ArmCapPolicy.from_dict(data, tmp_path)


def _arm(tmp_path: Path, **kwargs: Any) -> Any:
    return automerge_guard.arm_with_cap(
        "o/r",
        7,
        strategy="squash",
        delete_branch=False,
        arm_tier="agent",
        dispatch_kind="",
        repo_root=str(tmp_path),
        **kwargs,
    )


@pytest.fixture
def cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An enforced cap with room, backed by a store under ``tmp_path``."""
    policy = _policy(tmp_path)
    monkeypatch.setattr(arm_cap, "load_policy", lambda _root=None, policy_path=None: policy)
    monkeypatch.setattr(arm_cap, "DEFAULT_STORE_PATH", tmp_path / "cap.sqlite3")
    monkeypatch.delenv("RUNNER_DASHBOARD_STATE_DIR", raising=False)
    monkeypatch.setattr(automerge_guard, "_import_arm_cap_module", lambda: arm_cap)
    return arm_cap.ArmCapStore(tmp_path / "cap.sqlite3")


def _reservation_status(store: Any) -> str:
    with store._connect() as conn:  # noqa: SLF001
        row = conn.execute("SELECT status FROM arm_reservations WHERE repo = 'o/r' AND pr = 7").fetchone()
    return str(row["status"]) if row else ""


def _armed_result(*_a: object, **_k: object) -> Any:
    return automerge_guard.ArmResult(True, automerge_guard.HoldVerdict(False), "armed")


# --------------------------------------------------------------------------
# H-14: every gh call on the arm path has a timeout
# --------------------------------------------------------------------------


def _capture_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_run(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return _completed("ok")

    monkeypatch.setattr(automerge_guard.subprocess, "run", fake_run)
    return seen


def test_the_default_runner_bounds_every_gh_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(automerge_guard.GH_TIMEOUT_ENV, raising=False)
    seen = _capture_run(monkeypatch)
    automerge_guard._default_runner(["gh", "api", "user"])
    assert seen["timeout"] == automerge_guard.DEFAULT_GH_TIMEOUT_SECONDS


def test_the_timeout_can_be_set_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(automerge_guard.GH_TIMEOUT_ENV, "15")
    seen = _capture_run(monkeypatch)
    automerge_guard._default_runner(["gh", "api", "user"])
    assert seen["timeout"] == 15.0


@pytest.mark.parametrize("raw", ["", "abc", "0", "-3", "nan", "inf"])
def test_an_unusable_timeout_setting_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(automerge_guard.GH_TIMEOUT_ENV, raw)
    seen = _capture_run(monkeypatch)
    automerge_guard._default_runner(["gh", "api", "user"])
    assert seen["timeout"] == automerge_guard.DEFAULT_GH_TIMEOUT_SECONDS


def test_a_hung_gh_call_becomes_a_failed_call_not_a_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=list(cmd), timeout=kwargs["timeout"])

    monkeypatch.setattr(automerge_guard.subprocess, "run", hang)
    proc = automerge_guard._default_runner(["gh", "api", "repos/o/r/pulls/7"])
    assert proc.returncode == TIMED_OUT
    assert "timed out" in proc.stderr


def test_a_hold_check_that_times_out_does_not_arm(tmp_path: Path, cap: Any) -> None:
    calls: list[list[str]] = []

    def hung(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return _completed(returncode=TIMED_OUT, stderr="timed out after 120s: gh api")

    outcome = _arm(tmp_path, runner=hung)
    assert outcome.exit_code == 1
    assert outcome.armed is False
    assert "timed out" in outcome.reason
    assert not any("merge" in call for call in calls)
    assert _reservation_status(cap) == "released"


class _ArmCallTimesOut(FakeGh):
    """The arm call hangs; ``stored`` is what GitHub then reports for auto-merge."""

    def __init__(self, stored: str | None) -> None:
        super().__init__()
        self.stored = stored

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(cmd)
        if "pr" in argv and "merge" in argv:
            self.calls.append(argv)
            return _completed(returncode=TIMED_OUT, stderr="timed out after 120s: gh pr merge")
        if ".auto_merge.merge_method" in argv:
            self.calls.append(argv)
            if self.stored is None:
                return _completed(returncode=1, stderr="boom")
            return _completed(self.stored)
        return super().__call__(cmd)


def test_an_arm_call_that_times_out_but_landed_is_reported_armed() -> None:
    result = automerge_guard.arm_auto_merge("o/r", 7, runner=_ArmCallTimesOut("squash"))
    assert result.armed is True


def test_an_arm_call_that_times_out_and_did_not_land_is_not_armed() -> None:
    result = automerge_guard.arm_auto_merge("o/r", 7, runner=_ArmCallTimesOut("null"))
    assert result.armed is False
    assert result.auto_merge_may_be_on is False
    assert "timed out" in result.reason()


def test_an_arm_call_that_times_out_and_cannot_be_checked_may_be_on() -> None:
    result = automerge_guard.arm_auto_merge("o/r", 7, runner=_ArmCallTimesOut(None))
    assert result.armed is False
    assert result.auto_merge_may_be_on is True


# --------------------------------------------------------------------------
# B-22: the arm cap fails closed and gives its reservation back
# --------------------------------------------------------------------------


def test_an_unreadable_cap_policy_refuses_the_arm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(_root: object = None, policy_path: object = None) -> None:
        raise ValueError("cannot read dispatch limits policy")

    monkeypatch.setattr(arm_cap, "load_policy", unreadable)
    monkeypatch.setattr(automerge_guard, "_import_arm_cap_module", lambda: arm_cap)
    armed: list[int] = []
    monkeypatch.setattr(automerge_guard, "arm_auto_merge", lambda *a, **k: armed.append(1))
    outcome = _arm(tmp_path)
    assert outcome.exit_code == automerge_guard.EXIT_ARM_CAP_REFUSED
    assert "policy" in outcome.reason
    assert armed == []


def _broken_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **policy: object) -> list[int]:
    chosen = _policy(tmp_path, **policy)
    monkeypatch.setattr(arm_cap, "load_policy", lambda _root=None, policy_path=None: chosen)
    monkeypatch.setattr(automerge_guard, "_import_arm_cap_module", lambda: arm_cap)

    def boom(*_a: object, **_k: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(arm_cap, "admit_for_arm", boom)
    monkeypatch.setattr(arm_cap, "DEFAULT_STORE_PATH", tmp_path / "cap.sqlite3")
    armed: list[int] = []

    def arm(*_a: object, **_k: object) -> Any:
        armed.append(1)
        return _armed_result()

    monkeypatch.setattr(automerge_guard, "arm_auto_merge", arm)
    return armed


def test_a_broken_cap_store_refuses_the_arm_when_the_cap_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    armed = _broken_store(monkeypatch, tmp_path, shadow_mode=False)
    outcome = _arm(tmp_path)
    assert outcome.exit_code == automerge_guard.EXIT_ARM_CAP_REFUSED
    assert "database is locked" in outcome.reason
    assert armed == []


def test_a_broken_cap_store_does_not_block_a_shadow_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    armed = _broken_store(monkeypatch, tmp_path, shadow_mode=True)
    outcome = _arm(tmp_path)
    assert outcome.exit_code == 0
    assert armed == [1]


def test_an_arm_that_raises_gives_the_reservation_back(
    tmp_path: Path, cap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("gh api repos/o/r/pulls/7 failed: 502")

    monkeypatch.setattr(automerge_guard, "arm_auto_merge", boom)
    outcome = _arm(tmp_path)
    assert outcome.exit_code == 1
    assert outcome.armed is False
    assert "502" in outcome.reason
    assert "not verified" in outcome.message
    assert _reservation_status(cap) == "released"


def test_a_successful_arm_keeps_the_reservation(tmp_path: Path, cap: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(automerge_guard, "arm_auto_merge", _armed_result)
    outcome = _arm(tmp_path)
    assert outcome.exit_code == 0
    assert outcome.armed is True
    assert outcome.reason == ""
    assert _reservation_status(cap) == "confirmed"


def test_a_reservation_that_cannot_be_settled_does_not_change_the_arm_result(
    tmp_path: Path, cap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: object, token: str, **_k: object) -> bool:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(arm_cap.ArmCapStore, "confirm_reservation", boom)
    monkeypatch.setattr(automerge_guard, "arm_auto_merge", _armed_result)
    assert _arm(tmp_path).exit_code == 0


def test_a_shadow_cap_that_would_block_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _policy(tmp_path, shadow_mode=True, max_healthy_armed=0)
    monkeypatch.setattr(arm_cap, "load_policy", lambda _root=None, policy_path=None: policy)
    monkeypatch.setattr(arm_cap, "DEFAULT_STORE_PATH", tmp_path / "cap.sqlite3")
    monkeypatch.delenv("RUNNER_DASHBOARD_STATE_DIR", raising=False)
    monkeypatch.setattr(automerge_guard, "_import_arm_cap_module", lambda: arm_cap)
    monkeypatch.setattr(automerge_guard, "arm_auto_merge", _armed_result)
    outcome = _arm(tmp_path)
    assert outcome.exit_code == 0
    assert any("SHADOW would-block" in note for note in outcome.notes)


def test_the_cap_policy_is_found_from_another_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert automerge_guard._cap_root("") == Path(automerge_guard.__file__).resolve().parents[1]
    assert automerge_guard._cap_root(str(tmp_path)) == tmp_path.resolve()


# --------------------------------------------------------------------------
# Hold reasons reach the caller
# --------------------------------------------------------------------------


def test_a_held_pr_returns_its_hold_reasons(tmp_path: Path, cap: Any) -> None:
    outcome = _arm(tmp_path, runner=FakeGh(labels=["do-not-merge"]))
    assert outcome.exit_code == 1
    assert outcome.armed is False
    assert "do-not-merge" in outcome.reason
    assert _reservation_status(cap) == "released"


def test_a_cap_refusal_returns_its_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _policy(tmp_path, max_healthy_armed=0)
    monkeypatch.setattr(arm_cap, "load_policy", lambda _root=None, policy_path=None: policy)
    monkeypatch.setattr(arm_cap, "DEFAULT_STORE_PATH", tmp_path / "cap.sqlite3")
    monkeypatch.delenv("RUNNER_DASHBOARD_STATE_DIR", raising=False)
    monkeypatch.setattr(automerge_guard, "_import_arm_cap_module", lambda: arm_cap)
    outcome = _arm(tmp_path)
    assert outcome.exit_code == automerge_guard.EXIT_ARM_CAP_REFUSED
    assert "cap" in outcome.reason


def test_a_moved_head_returns_its_reason(tmp_path: Path, cap: Any) -> None:
    outcome = _arm(tmp_path, runner=FakeGh(), match_head_commit="ffff000")
    assert outcome.exit_code == 1
    assert "head moved" in outcome.reason


def test_the_cli_still_prints_the_result_line(
    tmp_path: Path, cap: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(automerge_guard, "arm_auto_merge", _armed_result)
    code = automerge_guard._arm_cli(
        "o/r", 7, strategy="squash", delete_branch=False, arm_tier="agent", dispatch_kind="", repo_root=str(tmp_path)
    )
    assert code == 0
    assert "armed o/r#7" in capsys.readouterr().out
