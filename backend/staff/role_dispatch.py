"""Resolve staff roles for dashboard dispatch."""

from __future__ import annotations

from staff.plan import RunRequest
from staff.roles import RoleSpec


def resolve_dispatch_role(roles: dict[str, RoleSpec], req: RunRequest) -> RoleSpec:
    role = roles.get(req.role)
    if role is None:
        raise ValueError(f"unknown role '{req.role}'")
    if not role.dispatchable:
        reason = (
            f"schema errors: {'; '.join(role.errors)}"
            if not role.valid
            else f"surface={role.surface}, retired={role.retired}"
        )
        raise ValueError(f"role '{req.role}' is not dispatchable from the dashboard ({reason})")
    return role
