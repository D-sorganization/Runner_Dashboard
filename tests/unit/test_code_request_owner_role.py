"""Code Requests are owned by product-owner, with barb as the rollout fallback (#1665).

Phase 2 of Repository_Management#1766 adds the ``product-owner`` role. Until a node's
Repository_Management checkout carries it, Code Requests stay with ``barb``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from code_requests.profiles import get_default_profiles
from staff.action_executors import (
    CODE_REQUEST_OWNER_ROLE,
    FALLBACK_CODE_REQUEST_OWNER_ROLE,
    code_request_owner_role,
    validate_action_default_roles,
)
from staff.actions import ACTION_REGISTRY, check_role_permission
from staff.roles import RoleSpec


def _spec(name: str) -> Any:
    return SimpleNamespace(name=name, dispatchable=True, retired=False, surface="dashboard")


def _roster(*names: str) -> dict[str, Any]:
    return {name: _spec(name) for name in names}


BASE = ("code-reviewer", "board-secretary", "barb")


@pytest.mark.unit
def test_product_owner_owns_code_requests() -> None:
    assert CODE_REQUEST_OWNER_ROLE == "product-owner"
    assert FALLBACK_CODE_REQUEST_OWNER_ROLE == "barb"


@pytest.mark.unit
def test_owner_is_product_owner_when_loaded() -> None:
    assert code_request_owner_role(_roster(*BASE, "product-owner")) == "product-owner"


@pytest.mark.unit
def test_owner_falls_back_to_barb_before_the_role_ships() -> None:
    assert code_request_owner_role(_roster(*BASE)) == "barb"


@pytest.mark.unit
def test_validation_accepts_the_barb_fallback() -> None:
    with (
        patch("staff.roles.roles_dir", return_value=Path("/mock/roles")),
        patch("staff.roles.load_roles", return_value=_roster(*BASE)),
    ):
        assert validate_action_default_roles(raise_on_error=True) == []


@pytest.mark.unit
def test_product_owner_may_run_code_request_actions_only() -> None:
    """The role schema has no allowed_actions key, so the owner role is granted by name."""
    spec = RoleSpec(name="product-owner", title="Product Owner", permissions={})
    with patch("staff.actions.load_roles", return_value={"product-owner": spec}):
        update = ACTION_REGISTRY.get("code_request.update")
        assert check_role_permission(update, "product-owner", role_spec=spec)
        hold = ACTION_REGISTRY.get("staff.hold")
        assert not check_role_permission(hold, "product-owner", role_spec=spec)


@pytest.mark.unit
def test_executor_profile_is_not_labelled_as_the_planner() -> None:
    by_id = {p.id: p for p in get_default_profiles()}
    assert by_id["planner-strong"].staff_role == "chief-architect"
    assert by_id["executor-cli"].staff_role == "issue-remediator"
