"""Panel seat scratch validation shared by the panel runner and trusted_agent_hook (#2899)."""

from __future__ import annotations

import tempfile
from pathlib import Path

PANEL_SCRATCH_PREFIX = "staff_panel_"
PANEL_SCRATCH_MARKER = ".staff-panel-fleet-root"


def discover_fleet_repo_root(start: Path | None = None) -> Path | None:
    """Return the nearest ancestor of ``start`` that contains ``fleet-hooks/manifest.yml``."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "fleet-hooks" / "manifest.yml").is_file():
            return candidate
    return None


def is_fleet_runner_dashboard_root(root: Path) -> bool:
    """True when ``root`` looks like a Runner_Dashboard checkout (not an arbitrary repo)."""
    resolved = root.resolve()
    return (
        (resolved / "fleet-hooks" / "manifest.yml").is_file()
        and (resolved / "backend" / "staff" / "panel.py").is_file()
        and (resolved / "scripts" / "agent-check-hook-bridge.py").is_file()
    )


def write_panel_scratch_marker(scratch_dir: Path, fleet_root: Path) -> None:
    """Record which fleet checkout created this panel scratch directory."""
    assert scratch_dir.is_dir(), f"scratch must exist: {scratch_dir}"  # noqa: S101
    assert is_fleet_runner_dashboard_root(fleet_root), f"invalid fleet root: {fleet_root}"  # noqa: S101
    (scratch_dir / PANEL_SCRATCH_MARKER).write_text(f"{fleet_root.resolve()}\n", encoding="utf-8")


def is_verified_panel_scratch(cwd: Path) -> bool:
    """True only for panel-runner ``staff_panel_*`` dirs under the system temp dir with a valid marker."""
    try:
        resolved = cwd.resolve()
    except OSError:
        return False
    if not resolved.is_dir() or resolved.is_symlink():
        return False
    if not resolved.name.startswith(PANEL_SCRATCH_PREFIX):
        return False
    tmp_root = Path(tempfile.gettempdir()).resolve()
    try:
        resolved.relative_to(tmp_root)
    except ValueError:
        return False
    marker = resolved / PANEL_SCRATCH_MARKER
    if not marker.is_file():
        return False
    try:
        fleet_root = Path(marker.read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        return False
    if not is_fleet_runner_dashboard_root(fleet_root):
        return False
    planted = discover_fleet_repo_root(resolved)
    if planted is not None and planted != fleet_root:
        return False
    return True
