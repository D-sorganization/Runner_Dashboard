"""Unit tests for deploy/runner_dashboard_env_check.py."""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "deploy" / "runner_dashboard_env_check.py"

_SPEC = importlib.util.spec_from_file_location("runner_dashboard_env_check", MODULE_PATH)
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)
inspect_env_file = _mod.inspect_env_file
atomic_set_gh_token = _mod.atomic_set_gh_token

_TOKEN = "gho_" + "f" * 36  # pragma: allowlist secret
_PREVIOUS = "gho_" + "a" * 36  # pragma: allowlist secret


def test_inspect_ok_for_clean_env(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.write_text("FLEET_NODES=hub\nDASHBOARD_PORT=8321\n", encoding="utf-8")
    report = inspect_env_file(env)
    assert report["ok"] is True
    assert report["issues"] == []


def test_inspect_detects_nul_bytes(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.write_bytes(b"FLEET_NODES=hub\n" + b"\x00" * 50 + b"TOKEN_LINE=old\n")
    report = inspect_env_file(env)
    assert report["ok"] is False
    kinds = {item["kind"] for item in report["issues"]}
    assert "nul_byte" in kinds


def test_inspect_detects_invalid_assignment(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.write_text("not-valid-line\nFOO=bar\n", encoding="utf-8")
    report = inspect_env_file(env)
    assert report["ok"] is False
    assert any(item["kind"] == "unparseable_line" for item in report["issues"])


def test_atomic_set_gh_token_preserves_other_lines_and_mode(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.write_text("FLEET_NODES=hub\n", encoding="utf-8")
    os.chmod(env, 0o600)
    atomic_set_gh_token(env, _PREVIOUS)
    atomic_set_gh_token(env, _TOKEN)

    text = env.read_text(encoding="utf-8")
    assert "FLEET_NODES=hub" in text
    assert _TOKEN in text
    assert _PREVIOUS not in text
    assert sum(1 for ln in text.splitlines() if ln.startswith("GH_TOKEN=")) == 1
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_atomic_set_strips_nul_bytes_from_rewrite(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.write_bytes(b"FLEET_NODES=hub\n" + b"\x00" * 10 + b"EXTRA=1\n")
    atomic_set_gh_token(env, _TOKEN)
    assert b"\x00" not in env.read_bytes()
    assert _TOKEN in env.read_text(encoding="utf-8")


def test_cli_json_exit_code(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.write_bytes(b"\x00BAD=1\n")
    proc = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--env-file", str(env), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert '"ok": false' in proc.stdout.lower() or '"ok": false' in proc.stdout
