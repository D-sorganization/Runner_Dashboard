"""Expert-panel tools for the fleet client (kept apart from fleet_tools.py).

``PanelClientMixin`` adds the client methods; ``panel_commands`` builds the CLI/MCP rows that
``fleet_tools.COMMANDS`` appends. API: ``/api/v1/staff/panels`` (staff.read to read, staff.chat to start).

Starting a panel spends model time, so ``panel-start`` is a CLI command only (no MCP tool), and the
hub's cost confirmation is never taken from the request body: callers pass ``confirm_cost`` on its own.
"""

from __future__ import annotations

from typing import Any

from fleet_validators import PATTERNS, _check, _match

PANELS = "/api/v1/staff/panels"
MAX_LIST = 100


class PanelClientMixin:
    """Methods mixed into ``FleetClient``; ``request`` is provided by the client."""

    def request(self, method: str, path: str, **kwargs: Any) -> Any:  # pragma: no cover - supplied by FleetClient
        raise NotImplementedError

    def panel_presets(self) -> Any:
        """Ready-made line-ups, with each seat's provider and whether it is available on the hub."""
        return self.request("GET", f"{PANELS}/presets")

    def panel_list(self, limit: int = 20, cursor: str | None = None) -> Any:
        """Recent panels, newest first."""
        in_range = isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= MAX_LIST
        _check(in_range, f"limit must be 1..{MAX_LIST}")
        return self.request("GET", PANELS, params={"limit": limit, "cursor": cursor})

    def panel_start(self, request: dict[str, Any], confirm_cost: bool = False) -> Any:
        """Start a panel from a hub ``PanelCreateRequest`` body; the hub validates every field.

        Pre: ``request`` is an object with a ``topic`` and an ``experts`` list, and no ``confirm_cost`` key.
        Post: 202 body with the thread and the cost estimate, or a FleetAPIError (400 when over threshold).
        """
        _check(isinstance(request, dict), "request must be a JSON object")
        _check(isinstance(request.get("topic"), str) and request["topic"].strip() != "", "request.topic is required")
        _check(isinstance(request.get("experts"), list), "request.experts must be a list (see panel-presets)")
        _check("confirm_cost" not in request, "pass confirm_cost on its own, not inside request")
        body = {**request, "confirm_cost": True} if confirm_cost is True else dict(request)
        return self.request("POST", PANELS, body=body, retry_idempotent=False)

    def panel_status(self, thread_id: str) -> Any:
        """Full panel state: turns, stances, consensus and synthesis."""
        return self.request("GET", f"{PANELS}/{_match(PATTERNS.run_id, thread_id, 'thread_id')}")

    def panel_digest(self, thread_id: str) -> Any:
        """Compact verdict (status, tally, synthesis excerpt), at most 4 KB."""
        return self.request("GET", f"{PANELS}/{_match(PATTERNS.run_id, thread_id, 'thread_id')}/digest")


_THREAD = {"type": "string", "maxLength": 120, "description": "Panel thread id (th_...)."}


def panel_commands(command_cls: Any) -> tuple[Any, ...]:
    """CLI rows for the panel methods; only the read paths are MCP tools."""
    return (
        command_cls("panel-presets", "panel_presets", "List expert-panel presets.", tool="fleet_panel_presets"),
        command_cls(
            "panel-list",
            "panel_list",
            "List recent expert panels.",
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIST},
                "cursor": {"type": "string", "maxLength": 200, "description": "next_cursor from a previous page."},
            },
            tool="fleet_panel_list",
        ),
        command_cls(
            "panel-start",
            "panel_start",
            "Start an expert panel (spends model time; CLI only).",
            {
                "request": {"type": "object", "description": "PanelCreateRequest JSON: topic, experts, rounds..."},
                "confirm_cost": {"type": "boolean", "description": "Accept an estimate over the cost threshold."},
            },
            required=("request",),
        ),
        command_cls(
            "panel-status",
            "panel_status",
            "Read a panel's turns, stances and synthesis.",
            {"thread_id": _THREAD},
            required=("thread_id",),
            tool="fleet_panel_status",
            positional=("thread_id",),
            untrusted=True,
        ),
        command_cls(
            "panel-digest",
            "panel_digest",
            "Read a panel's compact verdict.",
            {"thread_id": _THREAD},
            required=("thread_id",),
            tool="fleet_panel_digest",
            positional=("thread_id",),
            untrusted=True,
        ),
    )
