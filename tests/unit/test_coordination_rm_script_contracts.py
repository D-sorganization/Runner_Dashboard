"""Isolated contracts for RM subprocess results, discovery, and failures."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from coordination import rm_scripts
from coordination.rm_scripts import ScriptResult

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (' {"ok": true} \n', {"ok": True}),
        ('{\n "ok": true\n}', {"ok": True}),
        ('progress\n{"ok": true}\nfinished', {"ok": True}),
        ('{"old": 1}\n{"new": 2}', {"new": 2}),
        ('progress\n{"ok": true}\n[1, 2]', {"ok": True}),
        ("", None),
        (" \n ", None),
        ("not JSON", None),
        ("null", None),
        ("[]", None),
        ("42", None),
        ('"text"', None),
        ("{}", {}),
    ],
)
def test_result_extracts_last_json_object(stdout: str, expected: dict | None) -> None:
    assert ScriptResult(0, stdout, "").json() == expected


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (ScriptResult(1, '{"error": "denied", "errors": ["other"]}', "stderr"), "denied"),
        (ScriptResult(1, '{"error": 17}', ""), "17"),
        (ScriptResult(1, '{"errors": ["one", 2]}', ""), "one; 2"),
        (ScriptResult(1, '{"errors": []}', "detail"), '{"errors": []}detail'),
        (ScriptResult(1, '{"errors": "bad shape"}', ""), '{"errors": "bad shape"}'),
        (ScriptResult(7, "", ""), "exit code 7"),
        (ScriptResult(1, "x" * 400, "END"), "x" * 297 + "END"),
        (ScriptResult(1, "  out", "err  "), "outerr"),
    ],
)
def test_failure_reason_precedence_and_bounded_fallback(result: ScriptResult, expected: str) -> None:
    assert result.failure() == expected


def test_python_override_precedes_executable_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_RM_PYTHON", "/configured/python")
    finder = Mock(side_effect=AssertionError("override must not search PATH"))
    monkeypatch.setattr(rm_scripts.shutil, "which", finder)
    assert rm_scripts.python_for_rm() == "/configured/python"
    finder.assert_not_called()


@pytest.mark.parametrize(
    ("found", "expected"),
    [(["/bin/python3"], "/bin/python3"), ([None, "/bin/python"], "/bin/python"), ([None, None], "python")],
)
def test_python_discovery_fallbacks(monkeypatch: pytest.MonkeyPatch, found: list, expected: str) -> None:
    monkeypatch.setenv("STAFF_RM_PYTHON", "")
    monkeypatch.setattr(rm_scripts.shutil, "which", Mock(side_effect=found))
    assert rm_scripts.python_for_rm() == expected


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("missing interpreter"), PermissionError("denied"), subprocess.TimeoutExpired(["python"], 3)],
)
def test_invocation_errors_are_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception) -> None:
    monkeypatch.setattr(rm_scripts, "_rm_env", lambda: {"PATH": "/fake"})
    invoke = Mock(side_effect=error)
    monkeypatch.setattr(rm_scripts.subprocess, "run", invoke)
    result = rm_scripts.run_argv(["python", "-m", "scripts.example"], tmp_path, timeout=3)
    assert result == ScriptResult(130, "", str(error))
    assert result.failure() == str(error)[-300:]


@pytest.mark.parametrize(("stdout", "stderr"), [(None, None), ("output", "error")])
def test_invocation_preserves_exit_status_and_parameters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str | None, stderr: str | None
) -> None:
    env = {"PATH": "/fake"}
    monkeypatch.setattr(rm_scripts, "_rm_env", lambda: env)
    invoke = Mock(return_value=subprocess.CompletedProcess(["python"], 9, stdout, stderr))
    monkeypatch.setattr(rm_scripts.subprocess, "run", invoke)
    argv = ["python", "-m", "scripts.example", "argument with spaces"]
    result = rm_scripts.run_argv(argv, tmp_path, timeout=11)
    assert result == ScriptResult(9, stdout or "", stderr or "")
    invoke.assert_called_once_with(argv, cwd=str(tmp_path), capture_output=True, text=True, timeout=11, env=env)


def test_missing_checkout_does_not_invoke_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rm_scripts, "rm_root", lambda: None)
    invoke = Mock(side_effect=AssertionError("no checkout must not invoke a subprocess"))
    monkeypatch.setattr(rm_scripts, "run_argv", invoke)
    assert rm_scripts.run_module("example") == ScriptResult(127, "", rm_scripts.RM_MISSING)
    invoke.assert_not_called()


@pytest.mark.parametrize("explicit", [True, False])
def test_module_uses_explicit_or_discovered_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, explicit: bool
) -> None:
    discover = Mock(return_value=tmp_path)
    monkeypatch.setattr(rm_scripts, "rm_root", discover)
    monkeypatch.setattr(rm_scripts, "python_for_rm", lambda: "/fake/python")
    expected = ScriptResult(0, "{}", "")
    invoke = Mock(return_value=expected)
    monkeypatch.setattr(rm_scripts, "run_argv", invoke)
    result = rm_scripts.run_module("example", "--repo", "a/b", root=tmp_path if explicit else None, timeout=17)
    assert result is expected
    invoke.assert_called_once_with(["/fake/python", "-m", "scripts.example", "--repo", "a/b"], cwd=tmp_path, timeout=17)
    assert discover.call_count == (0 if explicit else 1)


def test_explicit_binary_precedes_wrapper_directory(tmp_path: Path) -> None:
    binary = tmp_path / "binary" / "gh"
    binary.parent.mkdir()
    binary.touch()
    assert (
        rm_scripts._find_fleet_gh_wrapper_dir(
            {"FLEET_GH_BIN": str(binary), "FLEET_GH_WRAPPER_DIR": str(tmp_path / "missing")}
        )
        == binary.parent
    )


def test_missing_binary_can_fall_back_to_explicit_directory(tmp_path: Path) -> None:
    (tmp_path / "gh").touch()
    assert (
        rm_scripts._find_fleet_gh_wrapper_dir(
            {"FLEET_GH_BIN": str(tmp_path / "missing"), "FLEET_GH_WRAPPER_DIR": str(tmp_path)}
        )
        == tmp_path
    )


@pytest.mark.parametrize("location", [".local/bin", ".claude/fleet-gh/bin", None])
def test_default_wrapper_search_is_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, location: str | None
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    expected = tmp_path / location if location else None
    if expected:
        expected.mkdir(parents=True)
        (expected / "gh").touch()
    assert rm_scripts._find_fleet_gh_wrapper_dir({}) == expected


def test_sanitized_environment_is_independent_and_preserves_app_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLEET_GH_APP", "explicit-app")
    monkeypatch.setenv("GH_TOKEN", "disallowed-fixture")
    monkeypatch.setattr(rm_scripts, "_find_fleet_gh_wrapper_dir", lambda env: tmp_path)
    before = dict(os.environ)
    result = rm_scripts._rm_env()
    assert result["FLEET_GH_APP"] == "explicit-app"
    assert "GH_TOKEN" not in result
    result["NEW_KEY"] = "changed"
    assert dict(os.environ) == before
