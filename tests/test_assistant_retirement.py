"""Tests for SC-D11 (issue #1330): Retirement of legacy assistant chat endpoint and
folding of codebase Q&A and Maxwell chat surfaces into the Staff Console.
"""

from __future__ import annotations

from staff.adapters import ADAPTERS, get_adapter
from staff.router import route_deterministic
from staff.router_models import ROLE_KEYWORD_RULES


def test_assistant_chat_returns_410_gone(make_authed_client, operator_principal) -> None:
    """POST /api/assistant/chat must return HTTP 410 Gone with Link header pointing to /api/v1/staff/threads."""
    client = make_authed_client(operator_principal)
    resp = client.post(
        "/api/assistant/chat",
        headers={"X-Requested-With": "XMLHttpRequest"},
        json={"prompt": "What is the runner fleet status?", "context": {"current_tab": "fleet"}},
    )
    assert resp.status_code == 410

    link_header = resp.headers.get("Link")
    assert link_header is not None
    assert "</api/v1/staff/threads>" in link_header
    assert 'rel="successor-version"' in link_header
    assert "Sunset" in resp.headers

    body = resp.json()
    assert "retired" in body.get("error", "").lower() or "retired" in body.get("detail", "").lower()
    assert body.get("redirect_url") == "/api/v1/staff/threads" or "/api/v1/staff/threads" in body.get("detail", "")


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


def test_maxwell_provider_adapter_registered() -> None:
    """Maxwell must be registered as a provider adapter in staff/adapters.py."""
    assert "maxwell" in ADAPTERS
    adapter = get_adapter("maxwell")
    assert adapter.provider_id == "maxwell"
