"""Minimum provider CLI versions and a cached ``--version`` probe (#1680).

A node whose CLI predates a flag the adapter passes fails every run with
``unknown option`` (seven DeskComputer runs on 2026-09-27: claude 2.1.79 vs
``--permission-prompts``). This module states each executable's floor, reads
the installed version once per binary, refuses runs and chat turns below the
floor with a non-retryable ``cli_outdated`` failure, and reports the same fact
for the roster so the dashboard shows it before anything is launched.

Floors were bisected against the published npm releases (linux-x64 binaries,
empty ``CLAUDE_CONFIG_DIR``):

  claude 2.1.259  first release that accepts ``--permission-prompts none``
                  (2.1.258: ``error: unknown option '--permission-prompts'``).
                  Every other unattended and chat flag, ``dontAsk`` included,
                  is already accepted by 2.1.79.

An installed CLI whose version cannot be read is *not* blocked: a flaky probe
must not become an outage, and the run-time classifier still reports a real
rejection as ``cli_outdated`` (#1669).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from staff.classifier import FailureClassification

Version = tuple[int, int, int]

# Keyed by executable, not provider: claude and claude-ollama share one binary and argv.
MIN_CLI_VERSIONS: dict[str, Version] = {"claude": (2, 1, 259)}

UPGRADE_HINTS: dict[str, str] = {
    "claude": "`claude update`, or `npm install -g @anthropic-ai/claude-code@latest` for an npm install",
}

PROBE_TIMEOUT_SECONDS = 15.0

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_lock = threading.Lock()
# executable -> (binary identity, probed version)
_cache: dict[str, tuple[tuple[str, int], Version | None]] = {}


def parse_version(text: str) -> Version | None:
    """First ``MAJOR.MINOR.PATCH`` in ``text`` (e.g. ``"2.1.280 (Claude Code)"``), else ``None``."""
    match = _VERSION_RE.search(text)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)


def _binary_key(path: str) -> tuple[str, int]:
    """Identity of the resolved binary: an upgrade re-points the symlink or rewrites the file."""
    real = os.path.realpath(path)
    try:
        return real, os.stat(real).st_mtime_ns
    except OSError:
        return real, 0


def _probe(path: str) -> Version | None:
    """Run ``<path> --version``. Post: never raises; ``None`` when the version cannot be read."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, path from shutil.which
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_version(proc.stdout) if proc.returncode == 0 else None


def installed_version(executable: str) -> Version | None:
    """Installed version of ``executable``, probed once per binary per process.

    Post: ``None`` when the executable is not on PATH or its version is unreadable.
    A changed binary (upgrade) is re-probed without a dashboard restart.
    """
    path = shutil.which(executable)
    if path is None:
        return None
    key = _binary_key(path)
    with _lock:
        hit = _cache.get(executable)
    if hit is not None and hit[0] == key:
        return hit[1]
    version = _probe(path)
    with _lock:
        _cache[executable] = (key, version)
    return version


def reset_cache() -> None:
    with _lock:
        _cache.clear()


@dataclass(frozen=True)
class CliVersionStatus:
    """What the roster reports for one floored executable."""

    executable: str
    installed: bool
    version: str | None
    min_version: str | None
    outdated: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cli_status(executable: str) -> CliVersionStatus:
    """Installed version of ``executable`` against its floor.

    Post: ``outdated`` is True only when a version was read and is below the floor;
    an executable without a floor is never probed.
    """
    floor = MIN_CLI_VERSIONS.get(executable)
    min_text = format_version(floor) if floor else None
    installed = shutil.which(executable) is not None if executable else False
    if floor is None or not installed:
        return CliVersionStatus(executable, installed, None, min_text, False, "" if installed else "not installed")
    version = installed_version(executable)
    if version is None:
        detail = f"{executable} --version could not be read; required {min_text}"
        return CliVersionStatus(executable, True, None, min_text, False, detail)
    found = format_version(version)
    if version < floor:
        hint = UPGRADE_HINTS.get(executable, f"upgrade {executable}")
        detail = f"{executable} CLI {found} < required {min_text}; upgrade the CLI on this node ({hint})"
        return CliVersionStatus(executable, True, found, min_text, True, detail)
    return CliVersionStatus(executable, True, found, min_text, False, "")


def is_outdated(executable: str) -> bool:
    return bool(executable) and executable in MIN_CLI_VERSIONS and cli_status(executable).outdated


def version_gate(executable: str) -> FailureClassification | None:
    """Refusal for launching ``executable`` below its floor, else ``None``.

    Post: a refusal is ``cli_outdated``, not retryable (a retry hits the same binary),
    and its remediation starts with ``"<exe> CLI X.Y.Z < required A.B.C; upgrade ..."``.
    """
    if not executable or executable not in MIN_CLI_VERSIONS:
        return None
    status = cli_status(executable)
    if not status.outdated:
        return None
    return FailureClassification(
        failure_class="cli_outdated",
        retryable=False,
        remediation=status.detail,
        error=f"{executable} CLI {status.version} is older than the minimum supported {status.min_version}",
    )


def provider_versions(adapters: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Roster ``provider_versions``: provider id -> :class:`CliVersionStatus` for floored executables."""
    out: dict[str, dict[str, Any]] = {}
    for pid, adapter in adapters.items():
        executable = getattr(adapter, "executable", "")
        if executable in MIN_CLI_VERSIONS:
            out[pid] = cli_status(executable).to_dict()
    return out
