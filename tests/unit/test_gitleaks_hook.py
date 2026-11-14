"""The gitleaks hook wrapper fails closed (#4451).

``gitleaks protect --staged`` exits 0 when its own ``git`` call fails, so a
broken toolchain reported "Passed" without scanning. At the pre-push stage it
also had nothing to scan, because nothing is staged at push time.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import gitleaks_hook

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT_FAILED_OUTPUT = (
    "3:04PM ERR [git] error: unknown option `staged'\n"
    '3:04PM ERR error="stderr is not empty"\n'
    "3:04PM INF 0 commits scanned.\n"
    "3:04PM INF no leaks found\n"
)


def _completed(returncode: int, output: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["gitleaks"], returncode=returncode, stdout=output, stderr="")


def test_staged_scan_is_used_when_no_push_range() -> None:
    command = gitleaks_hook.build_command({}, config=".gitleaks.toml")

    assert command[:3] == ["gitleaks", "protect", "--staged"]
    assert "--redact" in command


def test_push_range_is_scanned_at_pre_push() -> None:
    env = {"PRE_COMMIT_FROM_REF": "a" * 40, "PRE_COMMIT_TO_REF": "b" * 40}

    command = gitleaks_hook.build_command(env, config=".gitleaks.toml")

    assert command[:2] == ["gitleaks", "detect"]
    assert f"--log-opts={'a' * 40}..{'b' * 40}" in command
    assert "--staged" not in command


def test_a_ref_that_looks_like_an_option_is_rejected() -> None:
    env = {"PRE_COMMIT_FROM_REF": "--output=/tmp/x", "PRE_COMMIT_TO_REF": "b" * 40}

    with pytest.raises(ValueError, match="PRE_COMMIT_FROM_REF"):
        gitleaks_hook.build_command(env, config=".gitleaks.toml")


def test_clean_scan_passes() -> None:
    output = "3:04PM INF scanned ~68 bytes (68 bytes) in 166ms\n3:04PM INF no leaks found\n"

    assert gitleaks_hook.verdict(_completed(0, output)) == (0, "")


def test_leak_exit_code_is_kept() -> None:
    code, _ = gitleaks_hook.verdict(_completed(1, "3:04PM WRN leaks found: 1\n"))

    assert code == 1


def test_git_failure_with_exit_zero_fails_the_hook() -> None:
    code, reason = gitleaks_hook.verdict(_completed(0, GIT_FAILED_OUTPUT))

    assert code == gitleaks_hook.SCAN_DID_NOT_RUN
    assert "git" in reason


def test_missing_gitleaks_fails_the_hook() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(command[0])

    assert gitleaks_hook.main([], env={}, runner=runner) == gitleaks_hook.SCAN_DID_NOT_RUN


def test_main_returns_the_verdict_of_the_run() -> None:
    seen: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return _completed(0, GIT_FAILED_OUTPUT)

    assert gitleaks_hook.main([], env={}, runner=runner) == gitleaks_hook.SCAN_DID_NOT_RUN
    assert seen and seen[0][0] == "gitleaks"


def test_pre_commit_config_runs_the_wrapper() -> None:
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert "entry: python scripts/gitleaks_hook.py" in config
    assert "entry: gitleaks protect" not in config


@pytest.mark.skipif(shutil.which("gitleaks") is None or shutil.which("git") is None, reason="needs gitleaks and git")
def test_planted_fake_secret_fails_the_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q", ".")
    git("commit", "-q", "--allow-empty", "-m", "init")
    # Built at run time so that no scanner sees a secret-shaped literal in this file.
    fake = "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCYzzQp3xKEY12"
    (tmp_path / "leak.env").write_text(f'aws_secret_access_key = "{fake}"\n', encoding="utf-8")
    git("add", "leak.env")
    monkeypatch.chdir(tmp_path)

    code = gitleaks_hook.main(["--config", str(REPO_ROOT / ".gitleaks.toml")], env={})

    assert code != 0
