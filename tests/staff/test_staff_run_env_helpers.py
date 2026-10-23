"""Staff run helpers must work when the run's cwd is another repo's worktree (#2481)."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from staff import git_hooks, github_run_env

pytestmark = pytest.mark.unit


def _env() -> dict[str, str]:
    return github_run_env.build_staff_run_env(
        {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "/existing"},
        "tok",
        run_id="run-1",
        real_gh_path="gh",
    )


def test_run_env_prepends_backend_dir_and_keeps_existing_pythonpath() -> None:
    parts = _env()["PYTHONPATH"].split(os.pathsep)
    assert parts[0] == str(Path(github_run_env.__file__).resolve().parent.parent)
    assert (Path(parts[0]) / "staff" / "github_git_credential.py").is_file()
    assert parts[1:] == ["/existing"]


def test_run_env_makes_staff_importable_from_foreign_cwd(tmp_path: Path) -> None:
    env = _env()
    env.pop("PYTHONSAFEPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", "import staff.github_git_credential, staff.github_credential_client"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_pre_push_hook_references_existing_script_in_non_dashboard_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    hook = git_hooks.install_pre_push_hook(tmp_path)
    exec_line = hook.read_text(encoding="utf-8").splitlines()[-1]
    tokens = shlex.split(exec_line)
    assert tokens[0] == "exec" and tokens[1] == sys.executable
    script = Path(tokens[2])
    assert script.is_file() and script.name == "git_hooks.py"
    assert tmp_path not in script.parents
