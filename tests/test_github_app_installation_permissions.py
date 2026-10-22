"""GitHub App installation permission diagnostics and staff preflight (#2038)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from github_app_auth import (
    GhInstallationPermissionError,
    compare_installation_permissions,
    fetch_installation_permissions,
    mint_installation_access_token,
)
from staff.github_app_preflight import (
    attention_items_from_preflight,
    preflight_github_app_for_roles,
)
from staff.github_run_env import github_permissions_for_role
from staff.roles import RoleSpec

_REAL_422_BODY = json.dumps(
    {
        "message": "The level of access for permissions requested are not granted to this installation.",
    }
)

_REAL_INSTALLATION_BODY = {
    "id": 12345,
    "account": {"login": "D-sorganization", "type": "Organization"},
    "permissions": {
        "actions": "read",
        "checks": "read",
        "contents": "write",
        "issues": "write",
        "metadata": "read",
        "pull_requests": "write",
        "workflows": "read",
    },
}


def _role(**perms: Any) -> RoleSpec:
    base = {
        "lease": False,
        "push_branch": False,
        "open_pr": False,
        "merge": False,
        "host_shell": False,
        "notify_user": False,
    }
    base.update(perms)
    return RoleSpec(
        name="pr-remediator",
        title="PR Remediator",
        providers=("codex",),
        permissions=base,
    )


def test_compare_installation_permissions_detects_missing_write_level() -> None:
    requested = {"workflows": "write", "contents": "write", "metadata": "read"}
    granted = dict(_REAL_INSTALLATION_BODY["permissions"])
    missing = compare_installation_permissions(requested, granted)
    assert missing == ["workflows:write (installation has workflows:read)"]


def test_compare_installation_permissions_detects_absent_key() -> None:
    requested = {"organization_administration": "write"}
    granted = {"contents": "read"}
    missing = compare_installation_permissions(requested, granted)
    assert missing == ["organization_administration:write (installation has none)"]


def test_mint_installation_access_token_422_fetches_installation_and_reports_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "999")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")  # pragma: allowlist secret

    post_resp = MagicMock()
    post_resp.status_code = 422
    post_resp.text = _REAL_422_BODY

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = _REAL_INSTALLATION_BODY

    client = MagicMock()
    client.post.return_value = post_resp
    client.get.return_value = get_resp

    with patch("github_app_auth.github_app_private_key_configured", return_value=True):
        with patch("github_app_auth.build_app_jwt", return_value="jwt"):
            with patch("httpx.Client") as client_cls:
                client_cls.return_value.__enter__.return_value = client
                requested = {
                    **github_permissions_for_role(_role(push_branch=True, open_pr=True)),
                    "workflows": "write",
                }
                with pytest.raises(GhInstallationPermissionError) as excinfo:
                    mint_installation_access_token(
                        repositories=["D-sorganization/AffineDrift"],
                        permissions=requested,
                    )

    assert "workflows:write (installation has workflows:read)" in str(excinfo.value)
    client.get.assert_called_once()
    assert "installations/999" in client.get.call_args.args[0]


def test_fetch_installation_permissions_parses_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "999")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")  # pragma: allowlist secret

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = _REAL_INSTALLATION_BODY

    with patch("github_app_auth.github_app_private_key_configured", return_value=True):
        with patch("github_app_auth.build_app_jwt", return_value="jwt"):
            with patch("httpx.Client") as client_cls:
                client_cls.return_value.__enter__.return_value.get.return_value = get_resp
                granted = fetch_installation_permissions()

    assert granted["workflows"] == "read"
    assert granted["contents"] == "write"


def test_github_permissions_fleet_actions_do_not_request_workflows() -> None:
    """GitHub App only defines workflows:write; staff runs must not request workflows:read (#2169)."""
    role = _role(
        push_branch=True,
        open_pr=True,
        fleet_actions=["run.cancel", "queue.diagnose", "run.rerun"],
    )
    perms = github_permissions_for_role(role)
    assert "workflows" not in perms
    assert "actions" not in perms


def test_preflight_github_app_for_roles_ok_without_workflows_scope() -> None:
    role = _role(push_branch=True, open_pr=True, fleet_actions=["run.cancel"])
    roles = {"pr-remediator": role}
    granted = {k: v for k, v in _REAL_INSTALLATION_BODY["permissions"].items() if k != "workflows"}
    with patch("staff.github_app_preflight.github_app_auth_configured", return_value=True):
        with patch("staff.github_app_preflight.fetch_installation_permissions", return_value=granted):
            report = preflight_github_app_for_roles(roles)

    assert report["ok"] is True
    assert not report["mismatches"]


def test_attention_items_from_preflight() -> None:
    report = {
        "ok": False,
        "mismatches": [
            {
                "role": "pr-remediator",
                "missing": ["workflows:write (installation has workflows:read)"],
            }
        ],
    }
    items = attention_items_from_preflight(report)
    assert len(items) == 1
    assert items[0]["failure_class"] == "github_app_permissions"
    assert "pr-remediator" in items[0]["error"]
