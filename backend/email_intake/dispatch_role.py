"""Validate dispatch targets against published staff roles."""

from __future__ import annotations

from staff.roles import load_roles


class UnknownStaffRoleError(ValueError):
    """``role`` is not defined under staff/roles/*.yml."""


def normalize_staff_role(role: str) -> str:
    """Return canonical role id or raise when unknown."""
    name = role.strip().lower()
    if not name:
        raise UnknownStaffRoleError("role is required")
    if name not in load_roles():
        raise UnknownStaffRoleError(f"unknown staff role: {name}")
    return name
