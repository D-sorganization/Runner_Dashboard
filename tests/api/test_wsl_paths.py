"""Tests for platform_utils.wsl_paths — pure WSL path-normalisation utilities.

These tests are 100 % offline; no subprocesses, no filesystem side-effects
beyond tmp_path fixtures provided by pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the backend directory is on sys.path so the package is importable.
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from platform_utils.wsl_paths import (  # noqa: E402
    _candidate_wslconfig_paths,
    _dedupe_paths,
    _windows_path_to_wsl,
)


# ---------------------------------------------------------------------------
# _windows_path_to_wsl
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        # Basic drive conversion
        (r"C:\foo\bar", Path("/mnt/c/foo/bar")),
        (r"D:\Users\name", Path("/mnt/d/Users/name")),
        # Lowercase drive letter
        (r"c:\lowercase\path", Path("/mnt/c/lowercase/path")),
        # Mixed separators
        (r"C:/mixed/sep", Path("/mnt/c/mixed/sep")),
        # Uppercase drive retained as lowercase
        (r"Z:\deep\nested\dir", Path("/mnt/z/deep/nested/dir")),
        # Path with spaces
        (r"C:\Users\John Doe\Documents", Path("/mnt/c/Users/John Doe/Documents")),
        # Trailing backslash
        (r"C:\foo\bar\\", Path("/mnt/c/foo/bar/")),
        # Quoted path (real-world PowerShell output)
        ('"C:\\Program Files\\thing"', Path("/mnt/c/Program Files/thing")),
    ],
)
def test_windows_path_to_wsl_drive_conversion(raw: str, expected: Path) -> None:
    assert _windows_path_to_wsl(raw) == expected


@pytest.mark.parametrize(
    "raw, expected_path",
    [
        # UNC path — no drive letter; returned verbatim as Path
        (r"\\server\share\dir", Path(r"\\server\share\dir")),
        # Already a POSIX path — returned unchanged
        ("/mnt/c/already/posix", Path("/mnt/c/already/posix")),
        # Relative path — returned unchanged
        ("relative/path", Path("relative/path")),
    ],
)
def test_windows_path_to_wsl_non_drive_paths_returned_as_is(raw: str, expected_path: Path) -> None:
    """Non-drive-letter paths are returned as Path(raw.strip()) unchanged."""
    result = _windows_path_to_wsl(raw)
    assert isinstance(result, Path)
    assert result == expected_path


def test_windows_path_to_wsl_empty_string() -> None:
    """An empty string input returns Path('') without raising."""
    result = _windows_path_to_wsl("")
    assert isinstance(result, Path)


def test_windows_path_to_wsl_whitespace_stripped() -> None:
    """Leading/trailing whitespace is stripped before conversion."""
    result = _windows_path_to_wsl("  C:\\foo  ")
    assert result == Path("/mnt/c/foo")


# ---------------------------------------------------------------------------
# _dedupe_paths
# ---------------------------------------------------------------------------

def test_dedupe_paths_removes_duplicates() -> None:
    paths = [Path("/a"), Path("/b"), Path("/a"), Path("/c"), Path("/b")]
    result = _dedupe_paths(paths)
    assert result == [Path("/a"), Path("/b"), Path("/c")]


def test_dedupe_paths_preserves_insertion_order() -> None:
    paths = [Path("/z"), Path("/a"), Path("/m"), Path("/a")]
    result = _dedupe_paths(paths)
    assert result == [Path("/z"), Path("/a"), Path("/m")]


def test_dedupe_paths_empty_input() -> None:
    assert _dedupe_paths([]) == []


def test_dedupe_paths_all_unique() -> None:
    paths = [Path("/x"), Path("/y"), Path("/z")]
    assert _dedupe_paths(paths) == paths


# ---------------------------------------------------------------------------
# _candidate_wslconfig_paths
# ---------------------------------------------------------------------------

def test_candidate_wslconfig_paths_returns_list_of_paths() -> None:
    result = _candidate_wslconfig_paths()
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, Path), f"Expected Path, got {type(item)}: {item!r}"


def test_candidate_wslconfig_paths_no_duplicates() -> None:
    result = _candidate_wslconfig_paths()
    seen = set()
    for p in result:
        key = str(p)
        assert key not in seen, f"Duplicate candidate path: {p}"
        seen.add(key)


def test_candidate_wslconfig_paths_includes_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If WSL_KEEPALIVE_WSLCONFIG_PATH is set, it must appear in candidates."""
    custom = tmp_path / "custom.wslconfig"
    monkeypatch.setenv("WSL_KEEPALIVE_WSLCONFIG_PATH", str(custom))
    result = _candidate_wslconfig_paths()
    assert custom in result


def test_candidate_wslconfig_paths_userprofile_wsl_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """USERPROFILE env var should produce a /mnt/<drive>/…/.wslconfig candidate."""
    monkeypatch.setenv("USERPROFILE", r"C:\Users\TestUser")
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    monkeypatch.delenv("WSL_KEEPALIVE_WSLCONFIG_PATH", raising=False)
    monkeypatch.delenv("WSL_CONFIG_PATH", raising=False)

    result = _candidate_wslconfig_paths()
    expected = Path("/mnt/c/Users/TestUser/.wslconfig")
    assert expected in result
