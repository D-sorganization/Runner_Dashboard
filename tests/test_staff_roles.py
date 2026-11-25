"""Board-secretary stays inside the no-shell staff policy (#4656).

The issue cites ``Repository_Management/tests/test_staff_roles.py`` for the rule
that non-Barb roles keep ``host_shell`` false. This is the Runner Dashboard
check of that same constraint on the board-secretary role.
"""

from __future__ import annotations

import pytest
from staff.board_secretary_persona import board_secretary_role_data


@pytest.mark.unit
def test_board_secretary_may_not_merge_or_take_host_shell() -> None:
    permissions = board_secretary_role_data()["permissions"]
    assert permissions["merge"] is False
    assert permissions["host_shell"] is False
