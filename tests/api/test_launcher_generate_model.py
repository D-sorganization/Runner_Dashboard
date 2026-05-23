"""Tests for LauncherGenerateRequest pydantic model (issue #716)."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from models.requests import LauncherGenerateRequest
from pydantic import ValidationError


def test_missing_repo_full_raises():
    with pytest.raises(ValidationError):
        LauncherGenerateRequest()  # type: ignore[call-arg]


def test_invalid_repo_format_raises():
    with pytest.raises(ValidationError):
        LauncherGenerateRequest(repo_full="not-a-repo")


def test_valid_repo_accepted():
    req = LauncherGenerateRequest(repo_full="myorg/myrepo")
    assert req.repo_full == "myorg/myrepo"


def test_valid_repo_with_dots():
    req = LauncherGenerateRequest(repo_full="my.org/my-repo.js")
    assert req.repo_full == "my.org/my-repo.js"


def test_default_ref_is_main():
    req = LauncherGenerateRequest(repo_full="org/repo")
    assert req.ref == "main"


def test_custom_ref_accepted():
    req = LauncherGenerateRequest(repo_full="org/repo", ref="develop")
    assert req.ref == "develop"


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        LauncherGenerateRequest(repo_full="org/repo", unknown_field="bad")


def test_repo_full_too_short_raises():
    with pytest.raises(ValidationError):
        LauncherGenerateRequest(repo_full="a/")


def test_repo_full_with_numbers():
    req = LauncherGenerateRequest(repo_full="org123/repo456")
    assert req.repo_full == "org123/repo456"
