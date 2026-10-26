"""Fail-closed fleet-gh wrapper and credential helper (#2038, RD fleet-gh)."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

_FLEET_GH_DIR = Path(__file__).resolve().parents[2] / "deploy" / "fleet-gh"
_GH_APP = _FLEET_GH_DIR / "gh_app_credential.py"
_GH_SHIM = _FLEET_GH_DIR / "gh"
_GIT_CRED = _FLEET_GH_DIR / "git-credential-fleet-gh"
_INSTALLER = Path(__file__).resolve().parents[2] / "deploy" / "install-fleet-gh.sh"

_FAKE_GHS = "ghs_" + "F" * 40 + "_fleet"
_FAKE_GHP = "ghp_" + "P" * 36
_MINT_ERR = "fleet App token mint failed; refusing to fall back to personal auth"


def _load_module(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    mod = loader.load_module()
    return mod


@pytest.fixture
def gh_app():
    return _load_module("gh_app_credential", _GH_APP)


@pytest.mark.unit
def test_scrub_removes_inherited_github_tokens(gh_app: Any) -> None:
    env = gh_app.scrub_personal_github_tokens(
        {"PATH": "/bin", "GH_TOKEN": _FAKE_GHP, "GITHUB_TOKEN": _FAKE_GHP, "HOME": "/tmp"}
    )
    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["PATH"] == "/bin"


@pytest.mark.unit
def test_mint_failure_raises(gh_app: Any) -> None:
    with patch.object(gh_app, "_mint_installation_token", side_effect=RuntimeError("nope")):
        with pytest.raises(gh_app.FleetGhMintError, match=_MINT_ERR):
            gh_app.mint_fleet_app_token({})


@pytest.mark.unit
def test_gh_shim_exits_nonzero_on_mint_failure(gh_app: Any) -> None:
    with patch.object(gh_app, "mint_fleet_app_token", side_effect=gh_app.FleetGhMintError(_MINT_ERR)):
        proc = subprocess.run(
            [sys.executable, str(_GH_SHIM), "auth", "status"],
            env={
                **os.environ,
                "STAFF_REAL_GH": sys.executable,
                "PYTHONPATH": str(_FLEET_GH_DIR),
            },
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 3
    assert _MINT_ERR in proc.stderr
    assert "auth" not in proc.stdout


@pytest.mark.unit
def test_gh_shim_scrubs_inherited_token_before_real_gh(gh_app: Any) -> None:
    seen_env: dict[str, str] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen_env.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(argv, 0)

    gh_mod = _load_module("fleet_gh_shim_scrub", _GH_SHIM)
    fleet_env = {"GH_TOKEN": _FAKE_GHS, "GITHUB_TOKEN": _FAKE_GHS, "PATH": "/bin"}
    with patch.dict(os.environ, {"STAFF_REAL_GH": "/bin/true"}, clear=False):
        with patch.object(gh_mod, "apply_fleet_app_token", return_value=fleet_env):
            with patch.object(gh_mod, "subprocess") as mock_sub:
                mock_sub.run.side_effect = fake_run
                gh_mod.main()
    assert seen_env.get("GH_TOKEN") == _FAKE_GHS
    assert _FAKE_GHP not in seen_env.values()


@pytest.mark.unit
def test_git_credential_helper_fails_closed_on_mint_failure(gh_app: Any) -> None:
    cred = _load_module("git_credential_fleet_gh", _GIT_CRED)
    with patch.object(cred, "mint_fleet_app_token", side_effect=cred.FleetGhMintError(_MINT_ERR)):
        stdin = io.StringIO("protocol=https\nhost=github.com\n\n")
        out = io.StringIO()
        code = cred.main(stdin=stdin, stdout=out)
    assert code == 1
    assert out.getvalue() == ""


@pytest.mark.unit
def test_git_credential_helper_fails_closed_when_minter_is_unavailable(gh_app: Any) -> None:
    cred = _load_module("git_credential_fleet_gh", _GIT_CRED)
    with patch.object(gh_app, "_backend_root", return_value=None):
        stdin = io.StringIO("protocol=https\nhost=github.com\n\n")
        out = io.StringIO()
        code = cred.main(stdin=stdin, stdout=out)
    assert code != 0
    assert out.getvalue() == ""


@pytest.mark.unit
def test_installer_removes_existing_github_credential_helpers_before_installing_fleet_helper() -> None:
    text = _INSTALLER.read_text(encoding="utf-8")
    reset = "git config --global --unset-all credential.https://github.com.helper"
    install = 'git config --global credential.https://github.com.helper "${BIN_DIR}/git-credential-fleet-gh"'
    assert reset in text
    assert text.index(reset) < text.index(install)


@pytest.mark.unit
def test_installer_sets_staff_real_gh_and_prepends_path() -> None:
    text = _INSTALLER.read_text(encoding="utf-8")
    assert "STAFF_REAL_GH" in text
    assert "fleet-gh" in text
    assert "gh-personal" in text
