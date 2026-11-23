"""Extra identity scopes minted on staff run tokens, beyond fleet_actions (#4605)."""

from __future__ import annotations

from typing import Final

# Same role id as ``BOARD_PROPOSAL_ROLE`` in action_executors (not imported: that module
# validates roles at import time and would deadlock with tokens → run_token_scopes).
BOARD_CHAIR_ROLE = "board-secretary"

# Board runs propose ``board.convene`` through the action-proposal API, not free-form chat.
BOARD_SECRETARY_RUN_SCOPES: Final[tuple[str, ...]] = (
    "board.convene",
    "board.proposals.write",
    "staff.read",
)


def extra_run_scopes_for_role(role: str) -> tuple[str, ...]:
    """Return fixed API scopes for roles that must not receive full ``staff.chat``."""
    if role == BOARD_CHAIR_ROLE:
        return BOARD_SECRETARY_RUN_SCOPES
    return ()
