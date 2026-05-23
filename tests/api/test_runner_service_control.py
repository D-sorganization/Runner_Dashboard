"""Tests for runners.service_control — runner service lifecycle helpers.

All tests are fully offline. No real svc.sh or systemd calls are made.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import runners.service_control as sc  # noqa: E402
from runners.service_control import (  # noqa: E402
    RunnerUnit,
    _runner_limit,
    _runner_sort_key,
    get_runner_service_name,
    run_runner_svc,
    runner_num_from_id,
    runner_svc_path,
)


# ---------------------------------------------------------------------------
# RunnerUnit model
# ---------------------------------------------------------------------------

def test_runner_unit_model() -> None:
    unit = RunnerUnit(num=3, name="wsl-runner-keepalive.service", path=Path("/home/user/actions-runners/runner-3/svc.sh"))
    assert unit.num == 3
    assert isinstance(unit.path, Path)


# ---------------------------------------------------------------------------
# runner_svc_path
# ---------------------------------------------------------------------------

def test_runner_svc_path_returns_correct_path() -> None:
    path = runner_svc_path(1)
    assert isinstance(path, Path)
    assert path.name == "svc.sh"
    assert "runner-1" in str(path)


def test_runner_svc_path_runner_number_embedded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sc, "RUNNER_BASE_DIR", tmp_path)
    assert runner_svc_path(5) == tmp_path / "runner-5" / "svc.sh"


@pytest.mark.parametrize("num", [1, 2, 10, 99])
def test_runner_svc_path_parametrized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, num: int) -> None:
    monkeypatch.setattr(sc, "RUNNER_BASE_DIR", tmp_path)
    result = runner_svc_path(num)
    assert result == tmp_path / f"runner-{num}" / "svc.sh"


# ---------------------------------------------------------------------------
# run_runner_svc (async)
# ---------------------------------------------------------------------------

async def test_run_runner_svc_calls_run_cmd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sc, "RUNNER_BASE_DIR", tmp_path)
    captured: dict = {}

    async def fake_run_cmd(cmd, timeout=30, cwd=None):  # noqa: ANN001, ARG001
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return 0, "started", ""

    monkeypatch.setattr(sc, "run_cmd", fake_run_cmd)
    code, stdout, _ = await run_runner_svc(2, "start")
    assert code == 0
    assert "sudo" in captured["cmd"]
    assert "start" in captured["cmd"]
    # cwd should be the parent directory of svc.sh
    assert captured["cwd"] == tmp_path / "runner-2"


# ---------------------------------------------------------------------------
# runner_num_from_id
# ---------------------------------------------------------------------------

def test_runner_num_from_id_matches_local_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "HOSTNAME", "myhost")
    monkeypatch.setattr(sc, "RUNNER_ALIASES", [])
    runners = [{"id": 42, "name": "d-sorg-local-myhost-3"}]
    result = runner_num_from_id(42, runners)
    assert result == 3


def test_runner_num_from_id_wrong_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "HOSTNAME", "myhost")
    monkeypatch.setattr(sc, "RUNNER_ALIASES", [])
    runners = [{"id": 42, "name": "d-sorg-local-otherhost-3"}]
    result = runner_num_from_id(42, runners)
    assert result is None


def test_runner_num_from_id_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "HOSTNAME", "myhost")
    monkeypatch.setattr(sc, "RUNNER_ALIASES", [])
    runners = [{"id": 99, "name": "d-sorg-local-myhost-3"}]
    result = runner_num_from_id(42, runners)
    assert result is None


def test_runner_num_from_id_alias_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "HOSTNAME", "primary")
    monkeypatch.setattr(sc, "RUNNER_ALIASES", ["alias1", "alias2"])
    runners = [{"id": 7, "name": "d-sorg-local-alias1-5"}]
    result = runner_num_from_id(7, runners)
    assert result == 5


# ---------------------------------------------------------------------------
# _runner_limit
# ---------------------------------------------------------------------------

def test_runner_limit_returns_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "NUM_RUNNERS", 8)
    monkeypatch.setattr(sc, "MAX_RUNNERS", 12)
    assert _runner_limit() == 12


def test_runner_limit_num_greater(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "NUM_RUNNERS", 15)
    monkeypatch.setattr(sc, "MAX_RUNNERS", 10)
    assert _runner_limit() == 15


# ---------------------------------------------------------------------------
# _runner_sort_key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "runners, expected_order",
    [
        # Numeric sort: runner-2 before runner-10
        (
            [{"name": "d-sorg-local-host-10"}, {"name": "d-sorg-local-host-2"}],
            ["d-sorg-local-host-2", "d-sorg-local-host-10"],
        ),
        # Machine prefix sort: aaa before zzz
        (
            [{"name": "d-sorg-local-zzz-1"}, {"name": "d-sorg-local-aaa-1"}],
            ["d-sorg-local-aaa-1", "d-sorg-local-zzz-1"],
        ),
    ],
)
def test_runner_sort_key_ordering(runners: list[dict], expected_order: list[str]) -> None:
    sorted_runners = sorted(runners, key=_runner_sort_key)
    assert [r["name"] for r in sorted_runners] == expected_order


def test_runner_sort_key_no_number_suffix() -> None:
    """Runner names without a numeric suffix should sort last numerically."""
    runners = [{"name": "runner-nonnumeric"}, {"name": "runner-1"}]
    sorted_runners = sorted(runners, key=_runner_sort_key)
    # runner-1 has suffix 1, runner-nonnumeric has suffix 10^9 → runner-1 first
    assert sorted_runners[0]["name"] == "runner-1"


# ---------------------------------------------------------------------------
# get_runner_service_name
# ---------------------------------------------------------------------------

def test_get_runner_service_name_reads_dot_service_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sc, "RUNNER_BASE_DIR", tmp_path)
    svc_dir = tmp_path / "runner-3"
    svc_dir.mkdir()
    (svc_dir / ".service").write_text("actions.runner.org.runner-3.service\n")
    result = get_runner_service_name(3)
    assert result == "actions.runner.org.runner-3.service"


def test_get_runner_service_name_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sc, "RUNNER_BASE_DIR", tmp_path)
    monkeypatch.setattr(sc, "ORG", "D-sorganization")
    monkeypatch.setattr(sc, "HOSTNAME", "testhost")
    # No .service file created → should fall back to generated name
    result = get_runner_service_name(7)
    assert result is not None
    assert "7" in result
    assert "testhost" in result
