"""Linear read API router and shared runtime helpers."""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any

import linear_inventory
from fastapi import APIRouter, HTTPException
from linear_client import ApiKeyAuthProvider, LinearAPIError, LinearClient

router = APIRouter(prefix="/api/linear", tags=["linear"])

LINEAR_NOT_CONFIGURED_DETAIL = "Linear is not configured. Set LINEAR_API_KEY in ~/.config/runner-dashboard/env."
_LINEAR_CONFIG_PATH = Path(
    os.environ.get(
        "RUNNER_DASHBOARD_LINEAR_CONFIG",
        str(Path(__file__).resolve().parents[2] / "config" / "linear.json"),
    )
).expanduser()
_AUTH_STATUS_TTL = 30.0
_auth_status_cache: dict[str, tuple[str, float]] = {}


def load_linear_config() -> dict[str, Any]:
    """Load config/linear.json if it exists; otherwise return an empty config."""
    try:
        payload = json.loads(_LINEAR_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"workspaces": [], "mappings": {}}
    except (OSError, json.JSONDecodeError):
        return {"workspaces": [], "mappings": {}}
    return payload if isinstance(payload, dict) else {"workspaces": [], "mappings": {}}


def configured_workspaces(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only dict workspace entries from the Linear config."""
    workspaces = config.get("workspaces")
    if not isinstance(workspaces, list):
        return []
    return [workspace for workspace in workspaces if isinstance(workspace, dict)]


def workspace_env_var(workspace: dict[str, Any]) -> str:
    """Return the env var used to authorize one workspace."""
    auth = workspace.get("auth")
    if isinstance(auth, dict) and isinstance(auth.get("env"), str) and auth.get("env"):
        return str(auth["env"])
    return "LINEAR_API_KEY"


def build_linear_client(config: dict[str, Any]) -> LinearClient:
    """Build a Linear client using per-workspace env var overrides."""
    env_map = {
        str(workspace.get("id")): workspace_env_var(workspace)
        for workspace in configured_workspaces(config)
        if workspace.get("id")
    }
    return LinearClient(ApiKeyAuthProvider("LINEAR_API_KEY", env_map))


def has_configured_linear_key(config: dict[str, Any], workspace_id: str | None = None) -> bool:
    """Return whether any selected workspace has a non-empty API key env var."""
    for workspace in _select_workspaces(config, workspace_id):
        if os.environ.get(workspace_env_var(workspace), "").strip():
            return True
    return False


async def list_workspace_summaries(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return configured workspaces with a cached auth-status probe."""
    config = config or load_linear_config()
    workspaces = configured_workspaces(config)
    if not workspaces:
        return []

    client = build_linear_client(config)
    try:
        summaries = []
        for workspace in workspaces:
            teams = workspace.get("teams")
            summaries.append(
                {
                    "id": str(workspace.get("id") or ""),
                    "auth_kind": _workspace_auth_kind(workspace),
                    "auth_status": await _workspace_auth_status(workspace, client),
                    "teams_filter": teams if isinstance(teams, list) else ["*"],
                    "trigger_label": workspace.get("trigger_label") or "",
                    "default_repository": workspace.get("default_repository") or "",
                    "prefer_source": workspace.get("prefer_source") or "linear",
                }
            )
        return summaries
    finally:
        await client.aclose()


@router.get("/workspaces")
async def get_linear_workspaces() -> dict[str, list[dict[str, Any]]]:
    """List configured Linear workspaces with auth probe state."""
    return {"workspaces": await list_workspace_summaries()}


@router.get("/teams")
async def get_linear_teams(workspace: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """List Linear teams for one workspace or all configured workspaces."""
    config = load_linear_config()
    selected = _select_workspaces(config, workspace)
    if workspace and not selected:
        raise HTTPException(status_code=404, detail=f"Unknown Linear workspace '{workspace}'")
    if not selected:
        return {"teams": []}
    if not has_configured_linear_key(config, workspace):
        raise HTTPException(status_code=503, detail=LINEAR_NOT_CONFIGURED_DETAIL)

    client = build_linear_client(config)
    try:
        teams: list[dict[str, Any]] = []
        for item in selected:
            for team in await client.fetch_teams(str(item.get("id") or "")):
                entry = {
                    "id": team.get("id") or "",
                    "key": team.get("key") or "",
                    "name": team.get("name") or "",
                }
                if workspace is None:
                    entry["workspace_id"] = str(item.get("id") or "")
                teams.append(entry)
        return {"teams": teams}
    finally:
        await client.aclose()


@router.get("/issues")
async def get_linear_issues(
    workspace: str | None = None,
    team: str | None = None,
    state: str = "open",
    pickable_only: bool = False,
    complexity: list[str] | None = None,
    effort: list[str] | None = None,
    judgement: list[str] | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Return Linear-only issues normalized into the canonical issue shape."""
    config = _filter_config(load_linear_config(), workspace, team)
    if workspace and not configured_workspaces(config):
        raise HTTPException(status_code=404, detail=f"Unknown Linear workspace '{workspace}'")
    if not has_configured_linear_key(config, workspace):
        raise HTTPException(status_code=503, detail=LINEAR_NOT_CONFIGURED_DETAIL)

    client = build_linear_client(config)
    try:
        result = await linear_inventory.fetch_all_issues(
            config,
            client,
            state=state,
            pickable_only=pickable_only,
            complexity=list(complexity) if complexity else None,
            effort=list(effort) if effort else None,
            judgement=list(judgement) if judgement else None,
            limit=limit,
        )
    finally:
        await client.aclose()

    result["stats"] = {"linear_total": len(result.get("items", []))}
    return result


def _workspace_auth_kind(workspace: dict[str, Any]) -> str:
    auth = workspace.get("auth")
    if isinstance(auth, dict) and isinstance(auth.get("kind"), str) and auth.get("kind"):
        return str(auth["kind"])
    return "api_key"


def _select_workspaces(config: dict[str, Any], workspace_id: str | None) -> list[dict[str, Any]]:
    workspaces = configured_workspaces(config)
    if workspace_id is None:
        return workspaces
    return [workspace for workspace in workspaces if str(workspace.get("id") or "") == workspace_id]


def _filter_config(config: dict[str, Any], workspace_id: str | None, team: str | None) -> dict[str, Any]:
    filtered = copy.deepcopy(config)
    filtered["workspaces"] = _select_workspaces(filtered, workspace_id)
    if team:
        for workspace in configured_workspaces(filtered):
            workspace["teams"] = [team]
    return filtered


async def _workspace_auth_status(workspace: dict[str, Any], client: LinearClient) -> str:
    workspace_id = str(workspace.get("id") or "")
    env_var = workspace_env_var(workspace)
    cache_key = f"{workspace_id}:{env_var}"
    cached = _auth_status_cache.get(cache_key)
    if cached and (time.monotonic() - cached[1]) < _AUTH_STATUS_TTL:
        return cached[0]

    if not os.environ.get(env_var, "").strip():
        status = "missing_env"
    else:
        try:
            await client.fetch_workspace(workspace_id)
            status = "ok"
        except LinearAPIError as exc:
            status = "auth_failed" if exc.status_code in (401, 403) else "auth_failed"
        except Exception as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            status = "auth_failed"

    _auth_status_cache[cache_key] = (status, time.monotonic())
    return status
