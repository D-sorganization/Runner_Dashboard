"""Runner service lifecycle helpers.

Pure extraction from server.py of the functions that manage GitHub Actions
runner systemd services (svc.sh invocation, service name resolution, etc.).

Configuration values (ORG, RUNNER_BASE_DIR, runner limits, HOSTNAME, aliases)
are read live from :mod:`dashboard_config` — the single source of truth (issue
#943) — rather than re-derived from ``os.environ`` here. Tests monkeypatch
``dashboard_config`` to reconfigure them.
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path

import dashboard_config
from pydantic import BaseModel
from system_utils import run_cmd  # noqa: E402

log = logging.getLogger("dashboard")

# ---------------------------------------------------------------------------
# Configuration single source of truth (issue #943)
# ---------------------------------------------------------------------------
#
# These values are *re-exported* from dashboard_config rather than re-derived
# from os.environ. The previous duplicate derivation silently diverged: a
# RUNNER_BASE_DIR override honored by dashboard_config was ignored here, so
# metrics scanned one directory while sudo start/stop targeted another.
#
# They are read through module-level ``__getattr__`` so a test (or runtime
# reconfiguration) that monkeypatches ``dashboard_config.RUNNER_BASE_DIR`` is
# observed here without a stale snapshot, while ``service_control.RUNNER_BASE_DIR``
# stays importable for backwards compatibility and direct monkeypatching.


def __getattr__(name: str) -> object:
    """Re-export config from dashboard_config — single source of truth (#943).

    ``_runner_limit`` is kept as a backwards-compatible alias of the one true
    ``dashboard_config.runner_limit`` so existing ``from service_control import
    _runner_limit`` callers (server.py, tests) keep working while only one
    *definition* exists repo-wide.
    """
    if name == "_runner_limit":
        return dashboard_config.runner_limit
    if name in _CONFIG_REEXPORTS:
        return getattr(dashboard_config, _CONFIG_REEXPORTS[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_CONFIG_REEXPORTS = {
    "ORG": "ORG",
    "RUNNER_BASE_DIR": "RUNNER_BASE_DIR",
    "MAX_RUNNERS": "MAX_RUNNERS",
    "NUM_RUNNERS": "NUM_RUNNERS",
    "HOSTNAME": "HOSTNAME",
    "RUNNER_ALIASES": "RUNNER_ALIASES",
}


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class RunnerUnit(BaseModel):
    """Descriptor for a single runner service unit."""

    num: int
    name: str
    path: Path


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def runner_svc_path(runner_num: int) -> Path:
    """Return the path to a runner's svc.sh script.

    Pre-condition: runner_num is a positive int.
    Post-condition: returns a Path ending in svc.sh.
    """
    assert isinstance(runner_num, int) and runner_num > 0, f"runner_num must be a positive int, got {runner_num!r}"

    result = dashboard_config.RUNNER_BASE_DIR / f"runner-{runner_num}" / "svc.sh"
    assert result.name == "svc.sh"
    return result


async def run_runner_svc(
    runner_num: int,
    action: str,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Run a generated GitHub runner svc.sh from its own runner directory.

    Pre-condition: runner_num > 0; action is a non-empty string.
    """
    assert isinstance(runner_num, int) and runner_num > 0, f"runner_num must be a positive int, got {runner_num!r}"
    assert isinstance(action, str) and action, "action must be a non-empty string"

    svc_path = runner_svc_path(runner_num)
    return await run_cmd(
        ["sudo", str(svc_path), action],
        timeout=timeout,
        cwd=svc_path.parent,
    )


def runner_num_from_id(runner_id: int, runners: list[dict]) -> int | None:
    """Return the local runner number for a GitHub runner ID, or None.

    Only returns a number when the runner name's machine prefix matches
    HOSTNAME or a configured alias (case-insensitive).

    Pre-condition: runner_id is an int; runners is a list of dicts.
    """
    assert isinstance(runner_id, int), f"runner_id must be int, got {type(runner_id)!r}"
    assert isinstance(runners, list), f"runners must be list, got {type(runners)!r}"

    local_names = {
        dashboard_config.HOSTNAME.lower(),
        platform.node().lower(),
        *(alias.lower() for alias in dashboard_config.RUNNER_ALIASES),
    }
    for r in runners:
        name = r.get("name", "")
        parts = name.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit() and r["id"] == runner_id:
            machine = parts[0].removeprefix("d-sorg-local-").lower()
            if machine not in local_names:
                return None
            return int(parts[1])
    return None


def _runner_sort_key(runner: dict) -> tuple[str, int, str]:
    """Sort runner names by machine prefix then numeric suffix.

    Pre-condition: runner is a dict (may have a 'name' key).
    Post-condition: returns a 3-tuple for use with sorted().
    """
    assert isinstance(runner, dict), f"runner must be dict, got {type(runner)!r}"

    name = str(runner.get("name", ""))
    prefix, sep, suffix = name.rpartition("-")
    number = int(suffix) if sep and suffix.isdigit() else 10**9
    return (prefix.lower(), number, name.lower())


def get_runner_service_name(runner_num: int) -> str | None:
    """Return the systemd service name for a runner.

    Reads the .service file if present; otherwise falls back to a generated name.

    Pre-condition: runner_num > 0.
    """
    assert isinstance(runner_num, int) and runner_num > 0, f"runner_num must be a positive int, got {runner_num!r}"

    svc_file = dashboard_config.RUNNER_BASE_DIR / f"runner-{runner_num}" / ".service"
    if svc_file.exists():
        return svc_file.read_text().strip()
    # Fall back to common naming pattern
    return f"actions.runner.{dashboard_config.ORG}.d-sorg-local-{dashboard_config.HOSTNAME}-{runner_num}.service"
