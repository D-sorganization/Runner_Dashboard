"""Deployment drift detection for runner-dashboard nodes.

Compares deployed version metadata (from ``deployment.json``) against an
expected/latest version (typically the hub's ``VERSION`` file) and emits a
structured drift status that the UI can surface as an "Update available"
affordance.

The actual remote update (SSH / ansible / scheduled maintenance script) is
intentionally *not* implemented here — issue #572 calls for the guidance and
signal only. :func:`emit_update_signal` logs a structured notification that
downstream automation can pick up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.drift")

# Semantic version comparisons only need (major, minor, patch) as ints.
# A non-numeric component is treated as "unknown" and surfaces drift=True
# so operators still get a nudge.
_UNKNOWN = "unknown"


@dataclass(frozen=True)
class DriftStatus:
    """Result of comparing a deployed node's version to the expected version."""

    current: str
    expected: str
    drift: bool
    severity: str  # one of: "none", "patch", "minor", "major", "dirty", "unknown"
    dirty: bool
    git_sha: str
    git_branch: str
    deployed_at: str | None
    hostname: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "expected": self.expected,
            "drift": self.drift,
            "severity": self.severity,
            "dirty": self.dirty,
            "git_sha": self.git_sha,
            "git_branch": self.git_branch,
            "deployed_at": self.deployed_at,
            "hostname": self.hostname,
            "message": self.message,
            "update_available": self.drift and not self.dirty,
        }


def _parse_version(value: str) -> tuple[int, int, int] | None:
    """Return ``(major, minor, patch)`` or ``None`` if the version is unparseable."""
    if not value or value == _UNKNOWN:
        return None
    parts = value.strip().lstrip("v").split(".")
    if len(parts) < 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _classify_severity(
    current: tuple[int, int, int] | None,
    expected: tuple[int, int, int] | None,
) -> str:
    if current is None or expected is None:
        return "unknown"
    if current == expected:
        return "none"
    if current[0] != expected[0]:
        return "major"
    if current[1] != expected[1]:
        return "minor"
    return "patch"


def read_expected_version(version_file: Path) -> str:
    """Return the expected version string from a VERSION file, or ``"unknown"``."""
    try:
        for line in version_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped
    except (FileNotFoundError, OSError):
        return _UNKNOWN
    return _UNKNOWN


def evaluate_drift(
    deployment: dict[str, Any] | None,
    expected_version: str,
) -> DriftStatus:
    """Compare a deployment metadata dict against ``expected_version``.

    ``deployment`` is the payload returned by ``/api/deployment`` (a dict with
    keys like ``version``, ``git_sha``, ``git_dirty``, ``deployed_at``).
    """
    deployment = deployment or {}
    current = str(deployment.get("version") or _UNKNOWN)
    dirty = bool(deployment.get("git_dirty", False))

    current_parts = _parse_version(current)
    expected_parts = _parse_version(expected_version)
    severity = _classify_severity(current_parts, expected_parts)

    if dirty:
        severity = "dirty"
        drift = True
        message = f"Deployed tree is dirty (uncommitted changes) at {current}; redeploy from a clean checkout."
    elif severity == "none":
        drift = False
        message = f"Node is up to date at {current}."
    elif severity == "unknown":
        drift = True
        message = (
            f"Unable to compare versions (current={current}, expected={expected_version}); manual check recommended."
        )
    else:
        drift = True
        message = f"Update available: {current} → {expected_version} ({severity} drift)."

    return DriftStatus(
        current=current,
        expected=expected_version or _UNKNOWN,
        drift=drift,
        severity=severity,
        dirty=dirty,
        git_sha=str(deployment.get("git_sha") or _UNKNOWN),
        git_branch=str(deployment.get("git_branch") or _UNKNOWN),
        deployed_at=deployment.get("deployed_at"),
        hostname=deployment.get("hostname"),
        message=message,
    )


def emit_update_signal(
    node: str,
    status: DriftStatus,
    *,
    reason: str = "user-requested",
) -> dict[str, Any]:
    """Record a structured "update-requested" event for downstream automation.

    This deliberately does *not* SSH into the node or run ansible; remote
    update orchestration is deferred (see issue #572 acceptance criteria).
    Instead, we emit a well-shaped log line that ``scheduled-dashboard-
    maintenance.sh`` (or a future webhook consumer) can act on.
    """
    event = {
        "event": "dashboard.node.update_requested",
        "node": node,
        "current": status.current,
        "expected": status.expected,
        "severity": status.severity,
        "reason": reason,
        "dirty": status.dirty,
    }
    log.warning("update-signal %s", event)
    return event
