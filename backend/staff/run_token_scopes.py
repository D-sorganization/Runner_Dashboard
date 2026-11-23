"""Extra identity scopes minted on staff run tokens, beyond fleet_actions (#4605)."""

from __future__ import annotations

from typing import Final

from staff.action_executors import BOARD_PROPOSAL_ROLE

# Board runs propose ``board.convene`` through the action-proposal API, not free-form chat.
BOARD_SECRETARY_RUN_SCOPES: Final[tuple[str, ...]] = (
    "board.convene",
    "board.proposals.write",
    "staff.read",
)


def extra_run_scopes_for_role(role: str) -> tuple[str, ...]:
    """Return fixed API scopes for roles that must not receive full ``staff.chat``."""
    if role == BOARD_PROPOSAL_ROLE:
        return BOARD_SECRETARY_RUN_SCOPES
    return ()
