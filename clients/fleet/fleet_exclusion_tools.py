"""Dispatch-exclusion tools for the fleet client (#2544), kept apart from fleet_tools.py.

``ExclusionClientMixin`` adds the client methods; ``exclusion_commands`` builds the CLI/MCP rows that
``fleet_tools.COMMANDS`` appends. API: ``/api/v1/staff/exclusions`` (reads ``staff.read``, writes
``staff.holds.write``). A 403 is re-raised naming the missing scope.
"""

from __future__ import annotations

import re
from typing import Any

from fleet_validators import FleetAPIError, _check, _positive_int, _text

READ_SCOPE = "staff.read"
WRITE_SCOPE = "staff.holds.write"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BASE = "/api/v1/staff/exclusions"


class ExclusionClientMixin:
    """Methods mixed into ``FleetClient``; ``request`` and ``agent`` are provided by the client."""

    agent: str | None

    def request(self, method: str, path: str, **kwargs: Any) -> Any:  # pragma: no cover - supplied by FleetClient
        raise NotImplementedError

    def _exclusion_request(self, method: str, path: str, scope: str, **kwargs: Any) -> Any:
        try:
            return self.request(method, path, **kwargs)
        except FleetAPIError as exc:
            if exc.status != 403:
                raise
            hub = exc.body.get("detail") if isinstance(exc.body, dict) else None
            detail = f"missing scope {scope}: this token cannot {method} {path}" + (f" (hub: {hub})" if hub else "")
            body = {**exc.body, "detail": detail} if isinstance(exc.body, dict) else {"detail": detail}
            raise FleetAPIError(403, body) from None

    def exclusions_list(self) -> Any:
        """List dispatch exclusions (active and expired, flagged)."""
        return self._exclusion_request("GET", _BASE, READ_SCOPE)

    def exclusion_add(
        self,
        repo: str,
        reason: str,
        owner: str | None = None,
        issue: int | None = None,
        path_glob: str | None = None,
        expires_at: str | None = None,
    ) -> Any:
        """Exclude a repo/issue/path from automated dispatch. ``reason`` is mandatory; owner defaults to the agent."""
        who = owner if owner is not None else self.agent
        _check(bool(who), "owner is required (pass owner or configure an agent)")
        body: dict[str, Any] = {
            "repo": _text(repo, "repo", 120, form="line"),
            "reason": _text(reason, "reason", 500),
            "owner": _text(who, "owner", 500, form="line"),
        }
        if issue is not None:
            body["issue"] = _positive_int(issue, "issue")
        if path_glob is not None:
            body["path_glob"] = _text(path_glob, "path_glob", 200, form="line")
        if expires_at is not None:
            body["expires_at"] = _text(expires_at, "expires_at", 40, form="line")
        return self._exclusion_request("POST", _BASE, WRITE_SCOPE, body=body)

    def exclusion_remove(self, exclusion_id: str) -> Any:
        """Lift an exclusion by id (``excl-...`` from the list)."""
        _check(
            isinstance(exclusion_id, str) and bool(_ID.match(exclusion_id)), f"invalid exclusion_id: {exclusion_id!r}"
        )
        return self._exclusion_request("DELETE", f"{_BASE}/{exclusion_id}", WRITE_SCOPE)


def exclusion_commands(command_cls: Any) -> tuple[Any, ...]:
    """Build the CLI/MCP rows with ``fleet_tools.Command`` (passed in to avoid a circular import)."""
    return (
        command_cls(
            "exclusions list",
            "exclusions_list",
            "List dispatch exclusions (needs scope staff.read).",
            tool="fleet_exclusions_list",
        ),
        command_cls(
            "exclusions add",
            "exclusion_add",
            "Exclude a repo, issue or path glob from automated dispatch; a reason is required "
            "(needs scope staff.holds.write).",
            {
                "repo": {"type": "string", "description": "Repository name, e.g. Tools."},
                "reason": {"type": "string", "description": "Why dispatch must skip this (required)."},
                "owner": {"type": "string", "description": "Who owns the exclusion (defaults to the agent)."},
                "issue": {"type": "integer", "minimum": 1},
                "path_glob": {"type": "string", "maxLength": 200},
                "expires_at": {"type": "string", "description": "ISO-8601 expiry; omit for none."},
            },
            required=("repo", "reason"),
            tool="fleet_exclusion_add",
        ),
        command_cls(
            "exclusions remove",
            "exclusion_remove",
            "Lift a dispatch exclusion by id (needs scope staff.holds.write).",
            {"exclusion_id": {"type": "string", "description": "Exclusion id, e.g. excl-1a2b3c4d5e."}},
            required=("exclusion_id",),
            tool="fleet_exclusion_remove",
            positional=("exclusion_id",),
        ),
    )
