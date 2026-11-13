"""Keep the dispatcher protocol and its safety gates discoverable."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs/agents/grok.md"


def protocol() -> str:
    return GUIDE.read_text().split("## Dispatcher-First Protocol", 1)[1].split("\n## ", 1)[0]


@pytest.mark.parametrize(
    "requirement",
    [
        "hub_unreachable",
        "two probes",
        "terminally failed twice",
        "no provider available",
        "5 minutes",
        "live RD lease",
        "Sidekick packaging",
        "Assessment Board",
        "C3",
        "#2657",
        "Gasification_Model",
        "staff_thread_wait",
        "Idempotency-Key",
        "RM #2539",
        "subscription CLI",
        "ledger/check-in",
        "verified outcome",
    ],
)
def test_dispatcher_protocol_retains_gate(requirement: str) -> None:
    assert requirement in protocol()


def test_operating_guide_links_to_canonical_protocol() -> None:
    operating = (ROOT / "docs/operating-the-fleet-through-the-dashboard.md").read_text()
    assert "agents/grok.md#dispatcher-first-protocol" in operating


def test_protocol_marks_pending_client_implementation() -> None:
    assert "#3051" in protocol()
    assert "pending" in protocol()
