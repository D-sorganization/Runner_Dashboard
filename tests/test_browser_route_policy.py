from __future__ import annotations

import pytest
from browser_route_policy import classify_browser_route


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/", "shell"),
        ("GET", "/assets/app.js", "shell"),
        ("GET", "/api/auth/github", "access"),
        ("GET", "/api/auth/callback", "access"),
        ("GET", "/api/queue", "owner"),
        ("GET", "/api/fleet/status", "owner"),
        ("GET", "/api/runs", "owner"),
        ("GET", "/api/v1/staff/roster", "owner"),
        ("GET", "/api/v1/staff/groups/board/cost-estimate", "owner"),
        ("GET", "/api/v1/staff/groups/group_1/cost-estimate", "owner"),
        ("GET", "/api/v1/staff/threads", "owner"),
        ("GET", "/api/v1/staff/panels", "owner"),
        ("GET", "/api/v1/staff/threads/thread_1/events", "owner"),
        ("GET", "/api/v1/staff/panels/panel-1", "owner"),
        ("GET", "/api/v1/staff/proposals/p-1", "owner"),
        ("GET", "/api/v1/staff/runs/r-1/references", "owner"),
        ("GET", "/api/v1/staff/work-items/w-1", "owner"),
        ("POST", "/api/v1/staff/threads/thread_1/messages", "messages"),
        ("POST", "/api/v1/staff/panels", "dispatches"),
    ],
)
def test_browser_ui_routes_are_explicitly_classified(method: str, path: str, expected: str) -> None:
    policy = classify_browser_route(method, path)

    assert policy is not None
    assert policy.access == expected


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/runs/org/repo/cancel/42"),
        ("POST", "/api/queue/cancel-workflow"),
        ("POST", "/api/fleet/control/restart"),
        ("POST", "/api/agent-remediation/dispatch"),
        ("POST", "/api/coordination/messages"),
        ("GET", "/api/credentials"),
        ("GET", "/api/admin/principals"),
        ("POST", "/api/v1/staff/threads/thread_1/relay-card"),
        ("GET", "/api/v1/staff/threads/../events"),
        ("POST", "/api/v1/staff/groups/board/cost-estimate"),
        ("PUT", "/api/v1/staff/groups/board/cost-estimate"),
        ("GET", "/api/v1/staff/groups/board"),
        ("GET", "/api/v1/staff/groups/board/threads"),
        ("GET", "/api/v1/staff/groups/board/cost-estimate/extra"),
        ("GET", "/api/v1/staff/groups/board%2Fother/cost-estimate"),
        ("GET", "/api/not-a-ui-route"),
        ("POST", "/api/v1/staff/threads/thread_1"),
        ("POST", "/api/v1/staff/proposals/p-1/execute"),
        ("OPTIONS", "/api/queue"),
        ("TRACE", "/"),
    ],
)
def test_unknown_sensitive_and_machine_routes_fail_closed(method: str, path: str) -> None:
    assert classify_browser_route(method, path) is None


def test_auth_mutations_are_separately_classified_for_origin_checks() -> None:
    assert classify_browser_route("POST", "/api/auth/logout").access == "auth_mutation"
    assert classify_browser_route("POST", "/api/auth/refresh").access == "auth_mutation"
    assert classify_browser_route("POST", "/api/auth/browser-csrf").access == "owner"
    assert classify_browser_route("DELETE", "/api/auth/sessions/abc") is None
