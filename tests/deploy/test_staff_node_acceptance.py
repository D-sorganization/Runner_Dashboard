# test_staff_node_acceptance.py
"""Tests for deploy/staff-node-acceptance.sh (Runner_Dashboard#1273)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SCRIPT = DEPLOY / "staff-node-acceptance.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run([BASH, as_bash_path(SCRIPT), *args], capture_output=True, text=True)


def test_help_option() -> None:
    res = _run("--help")
    assert res.returncode == 0, res.stderr
    assert "Usage: staff-node-acceptance.sh" in res.stdout
    assert "--role" in res.stdout
    assert "--env-file" in res.stdout
    assert "--dropin-file" in res.stdout
    assert "--skip-service" in res.stdout


def test_invalid_role_fails() -> None:
    res = _run("--role", "invalid_role")
    assert res.returncode == 2
    assert "Invalid role" in res.stderr


def test_missing_env_file_fails(tmp_path: Path) -> None:
    fake_env = tmp_path / "nonexistent_env"
    fake_dropin = tmp_path / "nonexistent_dropin.conf"
    res = _run(
        "--role",
        "worker",
        "--env-file",
        as_bash_path(fake_env),
        "--dropin-file",
        as_bash_path(fake_dropin),
        "--skip-service",
        "--skip-network",
    )
    # The script should report failure since files don't exist and health endpoint is not up
    assert res.returncode == 1
    assert "Service env file missing" in res.stderr or "Service env file missing" in res.stdout


# --- #1276: live response shapes must not produce false failures ---------------

_HEALTH = '{"status":"healthy","deployment":{"git_sha":"08e6746","status":"blocked"}}'
_BOARD = (
    '{"providers":{"claude":true,"codex":true,"antigravity":true,"gemini":false,'
    '"cursor-agent":true,"ollama":true,"claude-ollama":true},'
    '"rm_source":{"status":"RM_STATUS","check_age_seconds":120}}'
)
_SCHEDULE = '{"enabled":true,"roles":[]}'


def _fake_node(tmp_path: Path, rm_status: str = "unchanged") -> tuple[Path, Path, Path]:
    """A fake bin dir whose curl returns the dashboard's real response shapes."""
    fake = tmp_path / "bin"
    fake.mkdir(parents=True)
    board = _BOARD.replace("RM_STATUS", rm_status)
    (fake / "curl").write_text(
        "#!/usr/bin/env bash\n"
        'url="${@: -1}"\n'
        'case "$url" in\n'
        f"  */api/health) echo '{_HEALTH}' ;;\n"
        f"  */api/staff/board*) echo '{board}' ;;\n"
        f"  */api/staff/schedule) echo '{_SCHEDULE}' ;;\n"
        '  */api/fleet/identity) echo \'{"name":"DeskComputer","role":"node","unregistered":false}\' ;;\n'
        "  *) exit 22 ;;\n"
        "esac\n",
        newline="\n",
    )
    for cli in ("gh", "node", "claude", "codex", "agy", "cursor-agent"):
        (fake / cli).write_text("#!/usr/bin/env bash\necho 'Logged in to github.com'\n", newline="\n")
    for exe in fake.iterdir():
        exe.chmod(0o755)
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "night-watch.yml").write_text("name: night-watch\n")
    gitcfg = tmp_path / "staff.gitconfig"
    gitcfg.write_text("[user]\n")
    env = tmp_path / "env"
    env.write_text(
        f"STAFF_SCHEDULER_ENABLED=1\nSTAFF_RM_ROOT={as_bash_path(tmp_path)}\n"
        f"STAFF_ROLES_DIR={as_bash_path(roles)}\nGIT_CONFIG_GLOBAL={as_bash_path(gitcfg)}\n",
        newline="\n",
    )
    return fake, env, tmp_path / "missing-dropin.conf"


def _run_fake(tmp_path: Path, *extra: str, rm_status: str = "unchanged") -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    fake, env, dropin = _fake_node(tmp_path, rm_status)
    cmd = (
        f'PATH="{as_bash_path(fake)}:$PATH" exec bash "{as_bash_path(SCRIPT)}" --role scheduler '
        f'--env-file "{as_bash_path(env)}" --dropin-file "{as_bash_path(dropin)}" '
        "--skip-service --skip-network " + " ".join(extra)
    )
    return subprocess.run([BASH, "-c", cmd], capture_output=True, text=True)


def test_live_shapes_report_no_false_failures(tmp_path: Path) -> None:
    res = _run_fake(tmp_path)
    out = res.stdout + res.stderr
    assert "health reports non-ok" not in out, out
    assert "Live board scheduler state matches expectation" in out, out
    assert "Provider available on board: cursor-agent" in out, out
    assert "Provider not marked available" not in out, out


def test_rm_source_error_status_fails(tmp_path: Path) -> None:
    out = (lambda r: r.stdout + r.stderr)(_run_fake(tmp_path, rm_status="error"))
    assert "[FAIL] Board rm_source" in out, out


def test_expect_sha_mismatch_fails(tmp_path: Path) -> None:
    good = _run_fake(tmp_path / "a", "--expect-sha", "08e6746")
    assert "Deployed commit matches" in good.stdout, good.stdout + good.stderr
    bad = _run_fake(tmp_path / "b", "--expect-sha", "1234567")
    assert "[FAIL] Deployed commit" in bad.stderr, bad.stdout + bad.stderr


def test_fleet_identity_mismatch_fails(tmp_path: Path) -> None:
    assert BASH is not None
    fake, env, dropin = _fake_node(tmp_path)
    # Set DISPLAY_NAME to WrongMachine so it mismatches DeskComputer returned by mock curl
    cmd = (
        f'DISPLAY_NAME="WrongMachine" PATH="{as_bash_path(fake)}:$PATH" exec bash "{as_bash_path(SCRIPT)}" '
        f'--role scheduler --env-file "{as_bash_path(env)}" --dropin-file "{as_bash_path(dropin)}" '
        "--skip-service --skip-network"
    )
    res = subprocess.run([BASH, "-c", cmd], capture_output=True, text=True)
    out = res.stdout + res.stderr
    assert "[FAIL] Local fleet identity mismatch" in out, out
