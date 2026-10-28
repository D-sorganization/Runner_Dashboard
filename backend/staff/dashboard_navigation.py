"""Allowlisted dashboard navigation action for Staff Console."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from staff.actions import ActionContext, ActionRegistry, ActionResult


# Keep in sync with frontend/src/shell/navRegistryData.ts. Values are canonical tab ids,
# never paths or URLs, so an approved chat action cannot escape the dashboard.
DASHBOARD_NAV_TABS = frozenset(
    {
        "staff",
        "fleet-command",
        "agent-bus",
        "library",
        "context",
        "queue",
        "remediation",
        "workflows",
        "code-requests",
        "projects",
        "overview",
        "operations",
        "insights",
        "security",
        "settings",
    }
)


def execute_dashboard_navigate(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    """Return one allowlisted in-app tab instruction for the dashboard shell."""
    from staff.actions import ActionResult

    tab_id = params.get("tab_id")
    if not isinstance(tab_id, str) or tab_id not in DASHBOARD_NAV_TABS:
        return ActionResult(
            success=False,
            error="tab_id must be a registered dashboard section",
            failure_class="invalid_params",
        )
    return ActionResult(success=True, result={"navigate_to": tab_id})


def register_dashboard_navigation_action(registry: ActionRegistry) -> None:
    """Register the read-only navigation action without bloating the main executor catalogue."""
    from staff.actions import ActionDefinition, ActionRiskClass

    registry.register(
        ActionDefinition(
            name="dashboard.navigate",
            description="Navigate the dashboard to a registered section after owner approval; use its exact tab id.",
            params_schema={"tab_id": "string"},
            required_scope="staff.chat",
            risk_class=ActionRiskClass.LOW,
            executor=execute_dashboard_navigate,
        )
    )
