"""Fire-time persona guards for StaffScheduler slots (#3869; 500-line cap split).

Picks the guard that may skip a due slot: Cartographer (#3788) and Lost Code Finder
(#3869). The Lost Code Finder guard is dormant until a ``lost-code-finder`` role file is
registered. The continuity store is passed as a factory so roles without a guard never
open it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from staff import cartographer_persona
from staff.barb_continuity import BarbContinuityStore
from staff.scheduler_cartographer import fire_guard as cartographer_fire_guard
from staff.scheduler_lost_code_finder import ROLE as LOST_CODE_FINDER_ROLE
from staff.scheduler_lost_code_finder import fire_guard as lost_code_finder_fire_guard


def persona_fire_guard(
    role_name: str,
    *,
    continuity: Callable[[], BarbContinuityStore],
    job: str | None,
    slot: str,
    now: datetime,
) -> tuple[str, str]:
    """``(cartographer_job, skip_reason)`` for a due slot; both empty when no guard applies."""
    if role_name == cartographer_persona.ROLE:
        cartographer_job = next(iter(cartographer_persona.CARTOGRAPHER_SCHEDULE_ENTRIES))
        reason = cartographer_fire_guard(role_name, continuity=continuity(), job=cartographer_job, slot=slot, now=now)
        return cartographer_job, reason
    if role_name == LOST_CODE_FINDER_ROLE:
        return "", lost_code_finder_fire_guard(role_name, continuity=continuity(), job=job or "", slot=slot, now=now)
    return "", ""
