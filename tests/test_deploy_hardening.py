"""Static regression checks for deploy hardening."""

from __future__ import annotations  # noqa: E402

import re  # noqa: E402
from pathlib import Path  # noqa: E402

_ROOT = Path(__file__).parent.parent
_DEPLOY = _ROOT / "deploy"
_DOCKERFILE = _ROOT / "Dockerfile"
_LOCK = _ROOT / "requirements.lock.txt"


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


# ---------------------------------------------------------------------------
# Dockerfile hardening checks (issue #415)
# ---------------------------------------------------------------------------


def test_dockerfile_pins_base_image_to_digest() -> None:
    """FROM must reference python:3.11.x-slim pinned to a sha256 digest."""
    content = _read(_DOCKERFILE)
    # Must NOT use a floating tag like python:3.11-slim without a digest
    assert "FROM python:3.11-slim\n" not in content
    # Must include a sha256 digest pin
    assert re.search(r"FROM python:3\.11\.\d+-slim@sha256:[a-f0-9]{64}", content), (
        "Dockerfile base image must be pinned to a specific sha256 digest, "
        "e.g. python:3.11.10-slim@sha256:<hash>"
    )


def test_dockerfile_installs_with_require_hashes() -> None:
    """pip install must use --require-hashes and reference the lock file."""
    content = _read(_DOCKERFILE)
    assert "--require-hashes" in content, (
        "Dockerfile pip install must use --require-hashes for supply-chain security"
    )
    assert "requirements.lock.txt" in content, (
        "Dockerfile must install from requirements.lock.txt (not plain requirements.txt)"
    )


def test_dockerfile_has_healthcheck() -> None:
    """HEALTHCHECK directive must be present and target /livez."""
    content = _read(_DOCKERFILE)
    assert "HEALTHCHECK" in content, "Dockerfile must include a HEALTHCHECK directive"
    assert "/livez" in content, "HEALTHCHECK must target the /livez endpoint"


def test_dockerfile_runs_as_non_root_user() -> None:
    """USER directive must be present and use a non-root UID (not root / 0)."""
    content = _read(_DOCKERFILE)
    assert re.search(r"^USER\s+(?!0\b|root\b)\S+", content, re.MULTILINE), (
        "Dockerfile must include a USER directive set to a non-root user"
    )


def test_requirements_lock_file_exists() -> None:
    """requirements.lock.txt must exist alongside requirements.txt."""
    assert _LOCK.exists(), (
        "requirements.lock.txt is missing; "
        "regenerate with: pip-compile --generate-hashes --output-file requirements.lock.txt requirements.txt"
    )


def test_requirements_lock_contains_hashes() -> None:
    """requirements.lock.txt must contain --hash= entries for supply-chain pinning."""
    content = _read(_LOCK)
    assert "--hash=sha256:" in content, (
        "requirements.lock.txt must contain sha256 hashes for every package"
    )
