"""Board seats must run on supported providers, never a deprecated one.

``gemini`` and ``grok-chat`` are retired fleet-wide (RM #2408); Bravo moved to
``antigravity`` so the Board keeps a Google model for diversity without the
deprecated Gemini CLI.
"""

from __future__ import annotations

import pytest
from staff.group_models import (
    BOARD_SEATS,
    DEPRECATED_SEAT_PROVIDERS,
    SeatSpec,
    validate_seat_providers,
)


@pytest.mark.unit
def test_deprecated_seat_providers_cover_gemini_and_grok_chat() -> None:
    assert {"gemini", "grok-chat"} <= DEPRECATED_SEAT_PROVIDERS


@pytest.mark.unit
def test_no_board_seat_uses_a_deprecated_provider() -> None:
    offenders = [s.name for s in BOARD_SEATS if s.provider in DEPRECATED_SEAT_PROVIDERS]
    assert offenders == []


@pytest.mark.unit
def test_bravo_runs_on_antigravity_gemini_flash_high() -> None:
    bravo = next(s for s in BOARD_SEATS if s.name == "bravo")
    assert bravo.provider == "antigravity"
    assert bravo.model == "gemini-3.8-flash-high"


@pytest.mark.unit
def test_validate_seat_providers_accepts_board_seats() -> None:
    assert validate_seat_providers(BOARD_SEATS) is BOARD_SEATS


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["gemini", "grok-chat"])
def test_validate_seat_providers_rejects_deprecated(provider: str) -> None:
    seat = SeatSpec(name="x", title="X", role="board-seat-x", provider=provider, model="m", mandate="m")
    with pytest.raises(ValueError, match=provider):
        validate_seat_providers((seat,))
