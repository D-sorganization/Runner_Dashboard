"""Tests for SC-D11 (issue #1330): Retirement of legacy assistant chat endpoint and
folding of codebase Q&A and Maxwell chat surfaces into the Staff Console.
"""

from __future__ import annotations

import pytest
from staff.adapters import ADAPTERS, get_adapter
from staff.router import route_deterministic
from staff.router_models import ROLE_KEYWORD_RULES


def test_cartographer_codebase_routing_keywords() -> None:
    """Cartographer must receive codebase navigation and structure queries."""
    assert "cartographer" in ROLE_KEYWORD_RULES
    rules = ROLE_KEYWORD_RULES["cartographer"]
    assert any("where is" in r for r in rules)
    assert any("codebase" in r for r in rules)

    decision = route_deterministic("Where is the job queue handled?")
    assert decision is not None
    assert decision.chosen_role == "cartographer"


def test_librarian_documentation_routing_keywords() -> None:
    """Librarian must receive documentation and endpoint explanation queries."""
    assert "librarian" in ROLE_KEYWORD_RULES
    rules = ROLE_KEYWORD_RULES["librarian"]
    assert any("explain endpoint" in r or "what does" in r for r in rules)

    decision = route_deterministic("What does /api/queue do?")
    assert decision is not None
    assert decision.chosen_role == "librarian"


def test_maxwell_routing_keywords() -> None:
    """Maxwell keywords route to maxwell role."""
    assert "maxwell" in ROLE_KEYWORD_RULES
    decision = route_deterministic("Check maxwell daemon status")
    assert decision is not None
    assert decision.chosen_role == "maxwell"


def test_maxwell_staff_provider_adapter_retired_from_routing() -> None:
    """Maxwell is no longer a selectable staff CLI provider (2026-10-22)."""
    assert "maxwell" not in ADAPTERS
    with pytest.raises(KeyError):
        get_adapter("maxwell")
