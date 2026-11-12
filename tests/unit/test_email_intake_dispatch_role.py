"""Staff role validation for email intake dispatch."""

import pytest

from email_intake.dispatch_role import UnknownStaffRoleError, normalize_staff_role
from email_intake.parse_request import parse_dispatch_hints
from staff.roles import RoleSpec


def test_parse_requires_role_line() -> None:
    assert parse_dispatch_hints("please fix CI") is None
    hints = parse_dispatch_hints("role: maintenance\nfix CI")
    assert hints is not None and hints.role == "maintenance"


def test_unknown_role_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "email_intake.dispatch_role.load_roles",
        lambda: {"maintenance": RoleSpec(name="maintenance", title="Maintenance")},
    )
    assert normalize_staff_role("maintenance") == "maintenance"
    with pytest.raises(UnknownStaffRoleError):
        normalize_staff_role("not-a-real-role")
