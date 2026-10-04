"""Diff-scoped pre-push pytest and CI-only heavy scans (issue #1864).

``scripts/run_pytest_diff.py`` and ``scripts/run_mypy_diff.py`` are vendored
from Repository_Management ``shared_scripts/`` (RM#1891, PR #1907). These
tests pin the behaviour this repository relies on and the pre-push hook wiring:
no bandit at pre-push (it runs in CI Standard's full tier), and pytest scoped
to the tests mapped from the changed files.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import run_pytest_diff

REPO_ROOT = Path(__file__).resolve().parents[1]
_HEAVY_SCANS = ("bandit", "semgrep", "radon", "pip-audit")


def _hooks() -> list[dict[str, Any]]:
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    return [hook for repo in config["repos"] for hook in repo.get("hooks", [])]


def _pre_push_hook(hook_id: str) -> dict[str, Any]:
    for hook in _hooks():
        if hook.get("id") == hook_id:
            return hook
    raise AssertionError(f"no pre-commit hook with id {hook_id!r}")


def _write(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def test_ok() -> None:\n    pass\n", encoding="utf-8")


def test_backend_module_maps_to_its_test_file(tmp_path: Path) -> None:
    _write(tmp_path, "backend/dispatch_routing.py")
    _write(tmp_path, "tests/test_dispatch_routing.py")
    assert run_pytest_diff.resolve_test_targets(["backend/dispatch_routing.py"], root=tmp_path) == [
        "tests/test_dispatch_routing.py"
    ]


def test_backend_router_maps_to_api_tests_by_name(tmp_path: Path) -> None:
    _write(tmp_path, "backend/routers/usage_metrics.py")
    _write(tmp_path, "tests/api/test_usage_metrics_routes.py")
    assert run_pytest_diff.resolve_test_targets(["backend/routers/usage_metrics.py"], root=tmp_path) == [
        "tests/api/test_usage_metrics_routes.py"
    ]


def test_changed_test_file_runs_itself(tmp_path: Path) -> None:
    _write(tmp_path, "tests/api/test_thing.py")
    assert run_pytest_diff.resolve_test_targets(["tests/api/test_thing.py"], root=tmp_path) == [
        "tests/api/test_thing.py"
    ]


def test_docs_and_frontend_changes_run_no_pytest(tmp_path: Path) -> None:
    assert run_pytest_diff.resolve_test_targets(["docs/x.md", "frontend/src/App.tsx", "SPEC.md"], root=tmp_path) == []


def test_unmapped_source_without_fallback_runs_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "backend/orphan_module.py")
    assert run_pytest_diff.resolve_test_targets(["backend/orphan_module.py"], root=tmp_path) == []


def test_no_targets_exits_zero_without_running_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("pytest must not run when nothing is mapped")

    monkeypatch.setattr(run_pytest_diff, "resolve_changed_files", lambda **kwargs: ["docs/x.md"])
    monkeypatch.setattr(run_pytest_diff.subprocess, "run", fail_run)
    assert run_pytest_diff.run_pytest_diff() == 0


def test_runs_pytest_on_mapped_targets_and_propagates_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "tests/test_foo.py")
    recorded: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, returncode=3)

    monkeypatch.setattr(run_pytest_diff.subprocess, "run", fake_run)
    assert run_pytest_diff.run_pytest_diff(files=["tests/test_foo.py"], root=tmp_path, extra_args=["-x"]) == 3
    assert recorded["cmd"][:3] == [sys.executable, "-m", "pytest"]
    assert "tests/test_foo.py" in recorded["cmd"]
    assert "-x" in recorded["cmd"]


def test_module_runs_via_python_dash_m_like_the_hook() -> None:
    """RM#1912: a script-path invocation could not import its sibling module."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.run_pytest_diff", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_vendored_files_name_their_upstream() -> None:
    for name in ("run_pytest_diff.py", "run_mypy_diff.py"):
        head = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8").splitlines()[:3]
        assert any(f"Vendored from Repository_Management shared_scripts/{name}" in line for line in head), name


def test_pre_push_pytest_is_diff_scoped_in_the_repo_environment() -> None:
    hook = _pre_push_hook("pytest-unit")
    assert hook["stages"] == ["pre-push"]
    assert "scripts.run_pytest_diff" in hook["entry"]
    # RM#1912: an isolated `language: python` venv cannot import the app.
    assert hook["language"] == "system"
    assert hook.get("pass_filenames") is False


@pytest.mark.parametrize("hook_id", _HEAVY_SCANS)
def test_heavy_scans_do_not_run_at_pre_push(hook_id: str) -> None:
    for hook in _hooks():
        if hook.get("id") == hook_id:
            assert "pre-push" not in hook.get("stages", []), f"{hook_id} runs in CI only"


def test_bandit_still_runs_in_ci() -> None:
    text = (REPO_ROOT / ".github" / "workflows" / "ci-standard.yml").read_text(encoding="utf-8")
    assert "bandit -r backend/" in text


def test_conftest_change_targets_its_directory_not_the_file(tmp_path: Path) -> None:
    """A conftest is not collectible; running it alone exits 5 (no tests)."""
    _write(tmp_path, "tests/clients/conftest.py")
    _write(tmp_path, "tests/clients/test_fleetctl.py")
    assert run_pytest_diff.resolve_test_targets(["tests/clients/conftest.py"], root=tmp_path) == ["tests/clients"]


def test_test_helper_change_targets_its_directory(tmp_path: Path) -> None:
    _write(tmp_path, "tests/clients/fleet_fixtures.py")
    _write(tmp_path, "tests/clients/test_fleetctl.py")
    assert run_pytest_diff.resolve_test_targets(["tests/clients/fleet_fixtures.py"], root=tmp_path) == ["tests/clients"]


def test_root_conftest_change_runs_the_fast_suite(tmp_path: Path) -> None:
    _write(tmp_path, "tests/conftest.py")
    assert run_pytest_diff.resolve_test_targets(["tests/conftest.py"], root=tmp_path) == ["tests"]


def test_direct_match_does_not_hide_other_matching_tests(tmp_path: Path) -> None:
    _write(tmp_path, "backend/gh_client.py")
    _write(tmp_path, "tests/test_gh_client.py")
    _write(tmp_path, "tests/test_gh_client_retry.py")
    _write(tmp_path, "tests/api/test_gh_client_routes.py")
    assert run_pytest_diff.resolve_test_targets(["backend/gh_client.py"], root=tmp_path) == [
        "tests/api/test_gh_client_routes.py",
        "tests/test_gh_client.py",
        "tests/test_gh_client_retry.py",
    ]
