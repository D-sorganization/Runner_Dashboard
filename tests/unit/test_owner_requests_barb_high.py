"""Owner requests to a Barb approve HIGH actions such as hold changes (#3647)."""

from __future__ import annotations

import pytest
from identity import Principal
from staff.owner_requests import is_barb_role, request_approves


def _owner() -> Principal:
    return Principal(id="human-owner", type="human", name="Owner", scopes=["staff.approve"])


@pytest.mark.parametrize("role", ["barb", "rd-barb", "claude-barb", "cursor-barb", "agy-barb"])
def test_barb_roles_detected(role: str) -> None:
    assert is_barb_role(role)


@pytest.mark.parametrize("role", [None, "", "maintenance", "issue-remediator", "barber"])
def test_non_barb_roles(role) -> None:
    assert not is_barb_role(role)


@pytest.mark.parametrize("action", ["staff.hold", "staff.unhold"])
def test_owner_request_to_barb_approves_holds(action: str) -> None:
    assert request_approves(_owner(), action, "rd-barb")
    assert request_approves(_owner(), action, "barb")


@pytest.mark.parametrize("action", ["staff.hold", "staff.unhold"])
def test_other_roles_still_need_a_tap_for_holds(action: str) -> None:
    assert not request_approves(_owner(), action, "maintenance")
    assert not request_approves(_owner(), action)


def test_requester_without_approve_scope_never_approves() -> None:
    nobody = Principal(id="x", type="human", name="X", scopes=["staff.read"])
    assert not request_approves(nobody, "staff.unhold", "rd-barb")
