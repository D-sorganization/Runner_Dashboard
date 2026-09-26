"""AgentDispatch is retired (#1499): its frontend page module is gone.

Contract:
- frontend/src/pages/AgentDispatch.tsx does not exist.
- /work/agent-dispatch and /agent-dispatch redirect to Staff Console.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_agent_dispatch_page_is_removed() -> None:
    assert not (_ROOT / "frontend" / "src" / "pages" / "AgentDispatch.tsx").exists()
