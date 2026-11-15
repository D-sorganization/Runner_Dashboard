"""Compare live agent_principals.yml to bundled repo defaults (#4257)."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from security import safe_yaml_load, validate_config_path
from staff.barb_lease_fence import BARB_SEAT_IDS

log = logging.getLogger("dashboard.agent_principals_drift")

_REPO_DEFAULTS = Path(__file__).resolve().parent.parent / "config" / "agent_principals.yml"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _agent_roles(path: Path) -> dict[str, list[str]]:
    allowed = [path.parent.resolve(), _REPO_DEFAULTS.parent.resolve()]
    validate_config_path(path, allowed_roots=allowed)
    data = safe_yaml_load(path, allowed_roots=allowed)
    if not isinstance(data, dict):
        return {}
    profile_roles = [str(r) for r in (data.get("assistant_profile") or {}).get("roles") or []]
    agents = data.get("agents")
    if not isinstance(agents, list):
        return {}
    out: dict[str, list[str]] = {}
    for row in agents:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        agent_id = str(row["id"])
        roles = [str(r) for r in row.get("roles") or []]
        combined = list(dict.fromkeys([*profile_roles, *roles]))
        out[agent_id] = combined
    return out


def _barb_role_gaps(repo_roles: dict[str, list[str]], live_roles: dict[str, list[str]]) -> list[str]:
    missing: list[str] = []
    for agent_id in sorted(BARB_SEAT_IDS):
        if "barb" not in repo_roles.get(agent_id, []):
            continue
        if "barb" not in live_roles.get(agent_id, []):
            missing.append(agent_id)
    return missing


def agent_principals_config_drift(
    *,
    repo_defaults: Path | None = None,
    live_path: Path | None = None,
) -> dict[str, Any]:
    """Report whether the live principals file matches the repo bundle."""
    repo_path = (repo_defaults or _REPO_DEFAULTS).resolve()
    if live_path is None:
        override = __import__("os").environ.get("DASHBOARD_AGENT_PRINCIPALS_FILE", "").strip()
        live = Path(override).expanduser().resolve() if override else repo_path
    else:
        live = live_path.resolve()

    if not repo_path.is_file():
        return {"checked": False, "reason": "repo defaults missing", "repo_path": str(repo_path)}
    if not live.is_file():
        return {"checked": False, "reason": "live principals file missing", "live_path": str(live)}

    repo_hash = _sha256(repo_path)
    live_hash = _sha256(live)
    sha_match = repo_hash == live_hash

    try:
        repo_roles = _agent_roles(repo_path)
        live_roles = _agent_roles(live)
    except (OSError, ValueError) as exc:
        log.warning("agent_principals_drift_parse_failed error_type=%s", type(exc).__name__)
        return {
            "checked": False,
            "reason": "parse failed",
            "repo_path": str(repo_path),
            "live_path": str(live),
        }

    barb_missing = _barb_role_gaps(repo_roles, live_roles)
    drifted = not sha_match or bool(barb_missing)

    return {
        "checked": True,
        "drifted": drifted,
        "repo_path": str(repo_path),
        "live_path": str(live),
        "repo_content_sha256": repo_hash,
        "live_content_sha256": live_hash,
        "content_sha256_match": sha_match,
        "barb_role_missing_on_live": barb_missing,
    }
