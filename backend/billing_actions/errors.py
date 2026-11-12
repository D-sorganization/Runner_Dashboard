"""Errors for the Actions-minutes billing monitor (#2878, slice 2)."""

from __future__ import annotations


class BillingPermissionGapError(RuntimeError):
    """The GitHub App lacks the billing-read permission (HTTP 401/403).

    Callers map this to the API's ``permission_gap`` status rather than
    falling back to a personal or owner token (explicit opt-in only, #2878).
    """

    def __init__(self, missing_permission: str = "Organization Administration: read") -> None:
        super().__init__(f"GitHub billing API denied access; grant '{missing_permission}' to the fleet App")
        self.missing_permission = missing_permission
