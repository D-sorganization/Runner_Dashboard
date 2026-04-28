"""Linear inventory fetch and normalization.

The cache TTL is intentionally shared with ``issue_inventory`` by importing its
``_CACHE_TTL`` constant, so Linear and GitHub inventory views expire on the
same cadence until a public shared cache module exists.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from typing import Any

from issue_inventory import _CACHE_TTL as CACHE_TTL
from issue_inventory import _age_hours, is_pickable
from linear_client import LinearClient
from linear_taxonomy_map import apply_mapping

log = logging.getLogger("dashboard")

OPEN_STATE_TYPES = ["triage", "backlog", "unstarted", "started"]
CLOSED_STATE_TYPES = ["completed", "canceled"]
GITHUB_ISSUE_URL_RE = re.compile(r"https?://github\.com/([^/]+/[^/]+)/issues/(\d+)")

_cache: dict[str, tuple[Any, float]] = {}


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is not None:
        data, ts = entry
        if time.monotonic() - ts < CACHE_TTL:
            return copy.deepcopy(data)
    return None


def _cache_set(key: str, data: Any) -> None:
    _cache[key] = (copy.deepcopy(data), time.monotonic())


async def fetch_workspace_issues(
    workspace: dict,
    mapping: dict,
    client: LinearClient,
    *,
    state: str = "open",
    team_keys: list[str] | None = None,
    limit: int = 500,
) -> tuple[list[dict], str | None]:
    """Fetch and normalize Linear issues for one workspace.

    Errors are returned instead of raised so callers can aggregate across
    workspaces even when one workspace has invalid credentials or a transient
    Linear API failure.
    """
    workspace_id = str(workspace.get("id") or "")
    try:
        raw_issues = await client.fetch_issues(
            workspace_id,
            team_keys=_team_filter(workspace, team_keys),
            state_types=_state_types(state),
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        msg = f"{workspace_id or '<unknown>'}: {exc}"
        log.warning("linear_inventory: %s", msg)
        return [], msg

    return [_normalise_issue(issue, mapping) for issue in raw_issues[:limit]], None


async def fetch_all_issues(
    config: dict,
    client: LinearClient,
    *,
    state: str = "open",
    pickable_only: bool = False,
    complexity: list[str] | None = None,
    effort: list[str] | None = None,
    judgement: list[str] | None = None,
    limit: int = 500,
) -> dict:
    """Aggregate configured Linear workspaces with issue_inventory-style filters."""
    limit = min(limit, 2000)
    complexity_set = set(complexity) if complexity else set()
    effort_set = set(effort) if effort else set()
    judgement_set = set(judgement) if judgement else set()

    workspaces = [workspace for workspace in config.get("workspaces", []) if isinstance(workspace, dict)]
    config_mappings = config.get("mappings")
    mappings: dict[Any, Any] = config_mappings if isinstance(config_mappings, dict) else {}
    cache_key = _cache_key(
        workspaces,
        state=state,
        pickable_only=pickable_only,
        complexity=complexity_set,
        effort=effort_set,
        judgement=judgement_set,
        limit=limit,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    tasks = []
    setup_errors: list[str] = []
    for workspace in workspaces:
        mapping_name = workspace.get("mapping")
        mapping = mappings.get(mapping_name) if isinstance(mapping_name, str) else None
        if not isinstance(mapping, dict):
            setup_errors.append(f"{workspace.get('id') or '<unknown>'}: missing mapping {mapping_name!r}")
            continue
        tasks.append(fetch_workspace_issues(workspace, mapping, client, state=state, limit=limit))

    results = await asyncio.gather(*tasks) if tasks else []
    items: list[dict] = []
    errors: list[str] = list(setup_errors)

    for workspace_items, error in results:
        if error:
            errors.append(error)
            continue
        for item in workspace_items:
            pickable, blocked_by = is_pickable(item, has_open_pr=item["linked_pr"] is not None)
            item["pickable"] = pickable
            item["pickable_blocked_by"] = blocked_by

            taxonomy = item.get("taxonomy") or {}
            if complexity_set and taxonomy.get("complexity") not in complexity_set:
                continue
            if effort_set and taxonomy.get("effort") not in effort_set:
                continue
            if judgement_set and taxonomy.get("judgement") not in judgement_set:
                continue
            if pickable_only and not pickable:
                continue
            items.append(item)

    items.sort(key=lambda item: item["age_hours"])
    result = {"items": items[:limit], "errors": errors}
    _cache_set(cache_key, result)
    return result


def _normalise_issue(issue: dict[str, Any], mapping: dict[str, Any]) -> dict:
    mapping_result = apply_mapping(issue, mapping)
    labels = list(mapping_result["derived_labels"])
    taxonomy = {key: value for key, value in mapping_result.items() if key not in {"derived_labels", "source_signals"}}
    issue_state = issue.get("state")
    state: dict[str, Any] = issue_state if isinstance(issue_state, dict) else {}
    state_type = state.get("type")
    github_attachments = _github_attachment_urls(issue)
    repository, number = _repository_and_number(github_attachments)

    return {
        "repository": repository,
        "number": number,
        "title": issue.get("title") or "",
        "url": issue.get("url") or "",
        "author": _person_name(issue.get("creator")),
        "assignees": [_person_name(issue.get("assignee"))] if _person_name(issue.get("assignee")) else [],
        "labels": labels,
        "state": "closed" if state_type in CLOSED_STATE_TYPES else "open",
        "age_hours": _age_hours(issue.get("createdAt") or ""),
        "taxonomy": taxonomy,
        "agent_claim": None,
        "claim_expires_at": None,
        "linked_pr": None,
        "pickable": False,
        "pickable_blocked_by": [],
        "linear": {
            "id": issue.get("id") or "",
            "identifier": issue.get("identifier") or "",
            "url": issue.get("url") or "",
            "team_key": _nested_str(issue, "team", "key"),
            "team_name": _nested_str(issue, "team", "name"),
            "priority": issue.get("priority") if isinstance(issue.get("priority"), int) else 0,
            "priority_label": issue.get("priorityLabel") or "",
            "estimate": issue.get("estimate") if isinstance(issue.get("estimate"), int) else None,
            "state_name": state.get("name") if isinstance(state.get("name"), str) else "",
            "state_type": state_type if isinstance(state_type, str) else "",
            "project_id": _nested_optional_str(issue, "project", "id"),
            "cycle_number": _nested_optional_int(issue, "cycle", "number"),
            "raw_label_names": _linear_label_names(issue),
            "github_attachments": github_attachments,
        },
        "sources": ["linear"],
    }


def _team_filter(workspace: dict, override: list[str] | None) -> list[str] | None:
    teams = override if override is not None else workspace.get("teams")
    if not isinstance(teams, list) or "*" in teams:
        return None
    return [team for team in teams if isinstance(team, str)]


def _state_types(state: str) -> list[str] | None:
    if state == "open":
        return OPEN_STATE_TYPES
    if state == "closed":
        return CLOSED_STATE_TYPES
    return None


def _github_attachment_urls(issue: dict[str, Any]) -> list[str]:
    attachments = issue.get("attachments")
    nodes = attachments.get("nodes") if isinstance(attachments, dict) else attachments
    if not isinstance(nodes, list):
        return []

    urls: list[str] = []
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("url"), str):
            continue
        match = GITHUB_ISSUE_URL_RE.search(node["url"])
        if match:
            urls.append(match.group(0))
    return urls


def _repository_and_number(urls: list[str]) -> tuple[str, int | None]:
    if not urls:
        return "", None
    match = GITHUB_ISSUE_URL_RE.search(urls[0])
    if not match:
        return "", None
    return match.group(1), int(match.group(2))


def _person_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("displayName", "name", "email"):
        person_value = value.get(key)
        if isinstance(person_value, str) and person_value:
            return person_value
    return ""


def _nested_str(source: dict[str, Any], parent: str, key: str) -> str:
    value = _nested_optional_str(source, parent, key)
    return value or ""


def _nested_optional_str(source: dict[str, Any], parent: str, key: str) -> str | None:
    node = source.get(parent)
    if not isinstance(node, dict):
        return None
    value = node.get(key)
    return value if isinstance(value, str) else None


def _nested_optional_int(source: dict[str, Any], parent: str, key: str) -> int | None:
    node = source.get(parent)
    if not isinstance(node, dict):
        return None
    value = node.get(key)
    return value if isinstance(value, int) else None


def _linear_label_names(issue: dict[str, Any]) -> list[str]:
    labels = issue.get("labels")
    nodes = labels.get("nodes") if isinstance(labels, dict) else labels
    if not isinstance(nodes, list):
        return []
    return [node["name"] for node in nodes if isinstance(node, dict) and isinstance(node.get("name"), str)]


def _cache_key(
    workspaces: list[dict],
    *,
    state: str,
    pickable_only: bool,
    complexity: set[str],
    effort: set[str],
    judgement: set[str],
    limit: int,
) -> str:
    workspace_parts = [
        f"{workspace.get('id')}:{workspace.get('mapping')}:{','.join(_team_filter(workspace, None) or ['*'])}"
        for workspace in workspaces
    ]
    return (
        f"linear|{','.join(sorted(workspace_parts))}|{state}|{pickable_only}"
        f"|{','.join(sorted(complexity))}|{','.join(sorted(effort))}"
        f"|{','.join(sorted(judgement))}|{limit}"
    )
