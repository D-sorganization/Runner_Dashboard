"""Extra identity scopes minted on staff run tokens, beyond fleet_actions (#4605)."""

from __future__ import annotations

from typing import Final

# Same role id as ``BOARD_PROPOSAL_ROLE`` in action_executors (not imported: that module
# validates roles at import time and would deadlock with tokens → run_token_scopes).
BOARD_CHAIR_ROLE = "board-secretary"

# Board runs need chat to convene and communicate with seats; approval remains separate.
BOARD_SECRETARY_RUN_SCOPES: Final[tuple[str, ...]] = (
    "board.convene",
    "board.proposals.write",
    "staff.read",
    "staff.chat",
)


def extra_run_scopes_for_role(role: str) -> tuple[str, ...]:
    """Return fixed API scopes for roles requiring additional scoped access."""
    if role == BOARD_CHAIR_ROLE:
        return BOARD_SECRETARY_RUN_SCOPES
    return ()
