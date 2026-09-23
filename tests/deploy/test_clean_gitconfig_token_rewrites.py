# test_clean_gitconfig_token_rewrites.py
"""Behavioral tests for deploy/clean-gitconfig-token-rewrites.sh (Runner_Dashboard#1216)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SCRIPT = DEPLOY / "clean-gitconfig-token-rewrites.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)

# Placeholder secrets: shaped like the CI-written entries, deliberately not real token formats.
SECRETS = ("not-a-real-secret-alpha", "not-a-real-secret-bravo", "not-a-real-secret-charlie")

GITCONFIG = f"""[user]
\tname = Runner User
\temail = runner@example.invalid
[url "https://x-access-token:{SECRETS[0]}@github.com/"]
\tinsteadof = https://github.com/
[url "https://github.com/"]
\tinsteadOf = git@github.com:
\tinsteadOf = ssh://git@github.com/
[url "https://x-access-token:{SECRETS[1]}@github.com/"]
\tinsteadof = https://github.com/
[core]
\tlongpaths = true
[url "https://{SECRETS[2]}@github.com/"]
\tpushInsteadOf = https://github.com/
[url "https://x-access-token:{SECRETS[0]}@github.com/"]
\tinsteadof = https://github.com/
"""


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run([BASH, as_bash_path(SCRIPT), *args], capture_output=True, text=True)


def _assert_no_secret(res: subprocess.CompletedProcess[str]) -> None:
    for secret in SECRETS:
        assert secret not in res.stdout
        assert secret not in res.stderr


def test_prunes_credential_rewrites_and_keeps_everything_else(tmp_path: Path) -> None:
    cfg = tmp_path / ".gitconfig"
    cfg.write_text(GITCONFIG, encoding="utf-8", newline="\n")

    res = _run("--target-file", as_bash_path(cfg))
    assert res.returncode == 0, f"script failed: {res.stderr}"
    _assert_no_secret(res)
    assert "***@github.com" in res.stdout

    cleaned = cfg.read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in cleaned
    assert "@github.com" not in cleaned.replace("git@github.com", "")
    assert "name = Runner User" in cleaned
    assert "longpaths = true" in cleaned
    assert '[url "https://github.com/"]' in cleaned
    assert "insteadOf = git@github.com:" in cleaned
    assert "insteadOf = ssh://git@github.com/" in cleaned

    backups = list(tmp_path.glob(".gitconfig.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == GITCONFIG
    if os.name != "nt":
        assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    cfg = tmp_path / ".gitconfig"
    cfg.write_text(GITCONFIG, encoding="utf-8", newline="\n")

    assert _run("--target-file", as_bash_path(cfg)).returncode == 0
    first = cfg.read_text(encoding="utf-8")

    res = _run("--target-file", as_bash_path(cfg))
    assert res.returncode == 0, res.stderr
    assert "No credential-bearing" in res.stdout
    assert cfg.read_text(encoding="utf-8") == first
    assert len(list(tmp_path.glob(".gitconfig.bak.*"))) == 1


def test_dry_run_changes_nothing_and_redacts(tmp_path: Path) -> None:
    cfg = tmp_path / ".gitconfig"
    cfg.write_text(GITCONFIG, encoding="utf-8", newline="\n")

    res = _run("--dry-run", "--target-file", as_bash_path(cfg))
    assert res.returncode == 0, res.stderr
    _assert_no_secret(res)
    assert "would remove 3 credential-bearing" in res.stdout
    assert cfg.read_text(encoding="utf-8") == GITCONFIG
    assert not list(tmp_path.glob(".gitconfig.bak.*"))


def test_clean_config_untouched(tmp_path: Path) -> None:
    content = '[user]\n\tname = Runner User\n[url "https://github.com/"]\n\tinsteadOf = git@github.com:\n'
    cfg = tmp_path / ".gitconfig"
    cfg.write_text(content, encoding="utf-8", newline="\n")

    res = _run("--target-file", as_bash_path(cfg))
    assert res.returncode == 0, res.stderr
    assert cfg.read_text(encoding="utf-8") == content
    assert not list(tmp_path.glob(".gitconfig.bak.*"))


def test_missing_target_is_noop(tmp_path: Path) -> None:
    res = _run("--target-file", as_bash_path(tmp_path / "absent.gitconfig"))
    assert res.returncode == 0, res.stderr
