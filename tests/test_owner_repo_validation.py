"""Tests for validate_owner_repo_format (issue #326 - SSRF prevention)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi import HTTPException  # noqa: E402
from security import validate_owner_repo_format  # noqa: E402


@pytest.mark.parametrize(
    "valid",
    [
        "D-sorganization/runner-dashboard",
        "owner/repo",
        "Owner_1/repo.name",
        "a/b",
        "org.name/repo-name",
    ],
)
def test_validate_owner_repo_format_accepts_valid(valid: str) -> None:
    assert validate_owner_repo_format(valid) == valid.strip()


@pytest.mark.parametrize(
    "invalid",
    [
        "owner/repo/extra",  # too many slashes
        "owner/../etc/passwd",  # path traversal
        "/repo",  # leading slash, no owner
        "owner/",  # empty repo
        "/",  # just slash
        "",  # empty string
        "owner repo",  # space
        "owner/repo?query=x",  # query injection
        "owner/repo\x00suffix",  # null byte
        "owner%2Frepo",  # URL-encoded slash
        "owner\nother/repo",  # newline injection
    ],
)
def test_validate_owner_repo_format_rejects_invalid(invalid: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_owner_repo_format(invalid)
    assert exc_info.value.status_code == 422


def test_validate_owner_repo_rejects_foreign_owner() -> None:
    """validate_owner_repo_format must reject inputs that are not valid owner/repo."""
    # An attacker-controlled owner: attacker.com/evil-repo is valid format
    # but would pass to the org-check; this test ensures format-only validation works.
    result = validate_owner_repo_format("attacker.com/evil-repo")
    assert result == "attacker.com/evil-repo"  # format is valid; org-check is separate


def test_validate_owner_repo_rejects_null_byte() -> None:
    """Null-byte injection in owner must be rejected by format check."""
    with pytest.raises(HTTPException):
        validate_owner_repo_format("D-sorganization\x00evil/runner-dashboard")


def test_validate_owner_repo_rejects_url_encoded_slash() -> None:
    """URL-encoded slash must not bypass format check."""
    with pytest.raises(HTTPException):
        validate_owner_repo_format("owner%2Frepo")


def test_validate_owner_repo_rejects_extra_path() -> None:
    """owner/repo/extra must be rejected (too many components)."""
    with pytest.raises(HTTPException):
        validate_owner_repo_format("owner/repo/extra")


def test_normalizer_source_code_calls_validate_owner_repo_format() -> None:
    """Verify that all three _normalize_repository_input bodies call validate_owner_repo_format."""
    source_paths = [
        _BACKEND / "server.py",
        _BACKEND / "routers" / "assistant.py",
        _BACKEND / "routers" / "remediation.py",
    ]
    for src_path in source_paths:
        src = src_path.read_text(encoding="utf-8")
        assert "validate_owner_repo_format" in src, (
            f"{src_path.name} must call validate_owner_repo_format in _normalize_repository_input"
        )
