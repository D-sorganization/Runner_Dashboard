"""WSL / Windows path normalisation utilities.

Pure functions with no side-effects beyond reading environment variables and
optionally iterating /mnt/c/Users.  Safe to import from any context.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

log = logging.getLogger("dashboard")


def _windows_path_to_wsl(raw_path: str) -> Path:
    """Convert a Windows path to its WSL mount equivalent when possible.

    Pre-condition: raw_path is a str.
    Post-condition: returns a Path object; never raises.
    """
    assert isinstance(raw_path, str), f"raw_path must be str, got {type(raw_path)!r}"

    normalized = raw_path.strip().strip('"')
    match = re.match(r"^([a-zA-Z]):[\\/](.*)$", normalized)
    if not match:
        result = Path(normalized)
        assert isinstance(result, Path)
        return result
    drive = match.group(1).lower()
    tail = match.group(2).replace("\\", "/")
    result = Path("/mnt") / drive / tail
    assert isinstance(result, Path)
    return result


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    """Return paths in insertion order with duplicates removed.

    Pre-condition: paths is a list of Path objects.
    Post-condition: returned list contains no duplicate string representations
                    and len(result) <= len(paths).
    """
    assert isinstance(paths, list), f"paths must be list, got {type(paths)!r}"

    seen: set[str] = set()
    deduped: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)

    assert len(deduped) <= len(paths)
    return deduped


def _candidate_wslconfig_paths() -> list[Path]:
    """Return plausible .wslconfig locations for the current user.

    Post-condition: returns a list of Path objects with no duplicates.
    """
    candidates: list[Path] = []

    for env_name in (
        "WSL_KEEPALIVE_WSLCONFIG_PATH",
        "WSL_CONFIG_PATH",
    ):
        raw = os.environ.get(env_name)
        if raw:
            candidates.append(Path(raw).expanduser())

    profile = os.environ.get("USERPROFILE")
    if profile:
        profile_path = Path(profile).expanduser()
        if os.name == "nt":
            candidates.append(profile_path / ".wslconfig")
        candidates.append(_windows_path_to_wsl(profile) / ".wslconfig")

    home_drive = os.environ.get("HOMEDRIVE")
    home_path = os.environ.get("HOMEPATH")
    if home_drive and home_path:
        windows_home = f"{home_drive}{home_path}"
        if os.name == "nt":
            candidates.append(Path(windows_home).expanduser() / ".wslconfig")
        candidates.append(_windows_path_to_wsl(windows_home) / ".wslconfig")

    users_root = Path("/mnt/c/Users")
    try:
        for profile_dir in users_root.iterdir():
            if not profile_dir.is_dir():
                continue
            if profile_dir.name.lower() in {
                "all users",
                "default",
                "default user",
                "public",
            }:
                continue
            candidates.append(profile_dir / ".wslconfig")
    except OSError:
        pass

    result = _dedupe_paths(candidates)
    assert isinstance(result, list)
    return result


def _resolve_powershell_executable() -> str | None:
    """Find a PowerShell executable from WSL service environments.

    Returns the first resolvable PowerShell path, or None if not found.
    """
    candidate_list: list[str | None] = [
        os.environ.get("POWERSHELL"),
        "powershell.exe",
        "pwsh.exe",
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "/mnt/c/Program Files/PowerShell/7/pwsh.exe",
    ]
    for candidate in candidate_list:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_absolute() and path.exists():
            return str(path)
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None
