"""Static regression checks for deploy hardening."""

from __future__ import annotations  # noqa: E402

from pathlib import Path  # noqa: E402

_ROOT = Path(__file__).parent.parent
_DEPLOY = _ROOT / "deploy"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_shared_deploy_lib_enables_strict_mode() -> None:
    content = _read(_DEPLOY / "lib.sh")
    assert "set -euo pipefail" in content


def test_update_deployed_requires_successful_backup() -> None:
    content = _read(_DEPLOY / "update-deployed.sh")
    assert 'backup_dir "$DEPLOY_DIR") || fail "Backup failed; aborting update"' in content
    assert 'fail "Backup returned empty path; aborting update"' in content


def test_refresh_token_requires_more_than_prefix() -> None:
    content = _read(_DEPLOY / "refresh-token.sh")
    assert "[A-Za-z0-9_]{30,}" in content


def test_setup_prefers_python_311_for_runtime_service() -> None:
    content = _read(_DEPLOY / "setup.sh")
    assert "command -v python3.11 || command -v python3" in content
    assert "ExecStart=${PYTHON_BIN} ${DEPLOY_DIR}/backend/server.py" in content
