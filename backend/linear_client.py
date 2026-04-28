"""Async Linear GraphQL client with pluggable authorization.

Linear uses intentionally different Authorization header formats depending on
the credential type. Personal API keys are sent as the raw key value, such as
``lin_api_xxx``. OAuth tokens are sent as ``Bearer xxx``. Auth providers return
the complete header value so the client never adds or assumes a prefix.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

_ISSUE_FIELDS = """
id
identifier
title
description
url
branchName
priority
priorityLabel
estimate
createdAt
updatedAt
state { id name type }
team { id key name }
assignee { id name email displayName }
creator { id name email displayName }
labels(first: 50) { nodes { id name color } }
attachments(first: 25) { nodes { url title source { type } } }
project { id name }
cycle { id number name }
parent { id identifier }
"""

_ISSUES_QUERY = f"""
query Issues($filter: IssueFilter, $first: Int, $after: String) {{
  issues(filter: $filter, first: $first, after: $after) {{
    nodes {{
      {_ISSUE_FIELDS}
    }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

_ISSUE_BY_IDENTIFIER_QUERY = f"""
query IssueByIdentifier($id: String!) {{
  issue(id: $id) {{
    {_ISSUE_FIELDS}
  }}
}}
"""

_TEAMS_QUERY = """
query Teams($first: Int) {
  teams(first: $first) {
    nodes { id key name description }
  }
}
"""

_WORKSPACE_QUERY = """
query Workspace { organization { id name urlKey } }
"""


class LinearAuthProvider(Protocol):
    """Return Authorization header values for a Linear workspace."""

    async def get_authorization(self, workspace_id: str) -> str:
        """Return the full Authorization header value."""
        ...


class LinearAPIError(Exception):
    """Raised for Linear transport, GraphQL, and auth failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        errors: list[dict[str, Any]] | None = None,
        workspace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.errors = errors or []
        self.workspace_id = workspace_id


class ApiKeyAuthProvider:
    """Read Linear personal API keys from environment variables.

    Linear personal API keys are sent without a ``Bearer`` prefix. The raw
    environment variable value is returned as the complete header value.
    """

    def __init__(self, env_var: str, workspaces: dict[str, str] | None = None) -> None:
        self.env_var = env_var
        self.workspaces = dict(workspaces or {})

    async def get_authorization(self, workspace_id: str) -> str:
        """Return the raw API key for a workspace."""
        env_var = self.workspaces.get(workspace_id, self.env_var)
        value = os.environ.get(env_var)
        if not value:
            raise LinearAPIError(
                f"Linear API key environment variable '{env_var}' is not set",
                workspace_id=workspace_id,
            )
        return value


class LinearClient:
    """Thin async GraphQL client for high-level Linear reads."""

    def __init__(
        self,
        auth: LinearAuthProvider,
        *,
        base_url: str = "https://api.linear.app/graphql",
        timeout: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.auth = auth
        self.base_url = base_url
        self.timeout = timeout
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        """Close the owned HTTP client, if this instance created one."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def fetch_issues(
        self,
        workspace_id: str,
        *,
        team_keys: list[str] | None = None,
        state_types: list[str] | None = None,
        updated_after: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return Linear issue nodes, paginating internally up to ``limit``."""
        if limit <= 0:
            return []

        collected: list[dict[str, Any]] = []
        cursor: str | None = None
        issue_filter = _build_issue_filter(team_keys, state_types, updated_after)

        while len(collected) < limit:
            page_size = min(100, limit - len(collected))
            data = await self._post_graphql(
                workspace_id,
                _ISSUES_QUERY,
                {"filter": issue_filter, "first": page_size, "after": cursor},
            )
            connection = data.get("issues")
            if not isinstance(connection, dict):
                return collected

            nodes = connection.get("nodes", [])
            if isinstance(nodes, list):
                collected.extend(node for node in nodes if isinstance(node, dict))

            page_info = connection.get("pageInfo", {})
            if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                break

        return collected[:limit]

    async def fetch_issue(self, workspace_id: str, identifier: str) -> dict[str, Any] | None:
        """Fetch one Linear issue by human-readable identifier."""
        data = await self._post_graphql(workspace_id, _ISSUE_BY_IDENTIFIER_QUERY, {"id": identifier})
        issue = data.get("issue")
        return issue if isinstance(issue, dict) else None

    async def fetch_teams(self, workspace_id: str) -> list[dict[str, Any]]:
        """Return Linear team nodes verbatim."""
        data = await self._post_graphql(workspace_id, _TEAMS_QUERY, {"first": 100})
        teams = data.get("teams")
        if not isinstance(teams, dict):
            return []
        nodes = teams.get("nodes", [])
        return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []

    async def fetch_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        """Return the Linear organization record for the workspace credentials."""
        data = await self._post_graphql(workspace_id, _WORKSPACE_QUERY, {})
        organization = data.get("organization")
        return organization if isinstance(organization, dict) else None

    async def _post_graphql(
        self,
        workspace_id: str,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        authorization = await self.auth.get_authorization(workspace_id)
        response = await self._http_client.post(
            self.base_url,
            json={"query": query, "variables": variables},
            headers={"Authorization": authorization, "Content-Type": "application/json"},
        )
        if response.status_code != 200:
            raise LinearAPIError(
                f"Linear GraphQL request failed with HTTP {response.status_code}",
                status_code=response.status_code,
                workspace_id=workspace_id,
            )

        payload = response.json()
        if not isinstance(payload, dict):
            raise LinearAPIError("Linear GraphQL response was not a JSON object", workspace_id=workspace_id)

        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            raise LinearAPIError(
                "Linear GraphQL response contained errors",
                status_code=response.status_code,
                errors=[error for error in errors if isinstance(error, dict)],
                workspace_id=workspace_id,
            )

        data = payload.get("data", {})
        return data if isinstance(data, dict) else {}


def _build_issue_filter(
    team_keys: list[str] | None,
    state_types: list[str] | None,
    updated_after: str | None,
) -> dict[str, Any] | None:
    issue_filter: dict[str, Any] = {}
    if team_keys:
        issue_filter["team"] = {"key": {"in": team_keys}}
    if state_types:
        issue_filter["state"] = {"type": {"in": state_types}}
    if updated_after:
        issue_filter["updatedAt"] = {"gt": updated_after}
    return issue_filter or None
