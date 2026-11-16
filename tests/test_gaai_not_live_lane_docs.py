"""User-facing docs must not advertise GAAI as a live remediation lane (RM #2726, epic #2720).

GAAI is retired (RM #2725 / #2727): the Runner Dashboard has no live GAAI provider and
the roster no longer includes a ``gaai`` identity. These checks cover the README, the
SPEC, the C4 map, and the roster file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md", "SPEC.md", "docs/architecture/C4.md")
# Dispatch phrasing that names GAAI next to the agents Runner Dashboard actually dispatches.
LIVE_LANE = re.compile(r"(Jules,\s*GAAI|GAAI/Claude|GAAI,\s*Claude)", re.IGNORECASE)


@pytest.mark.parametrize("relative_path", DOCS)
def test_doc_does_not_list_gaai_as_dispatch_agent(relative_path: str) -> None:
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    hits = [
        f"{relative_path}:{number}: {line.strip()[:90]}"
        for number, line in enumerate(text.splitlines(), start=1)
        if LIVE_LANE.search(line)
    ]
    assert not hits, "GAAI is quarantined (RM #2720); remove it from dispatch copy:\n" + "\n".join(hits)


def test_roster_does_not_include_gaai() -> None:
    roster = (REPO_ROOT / "backend" / "coordination" / "roster.py").read_text(encoding="utf-8")
    assert '"gaai"' not in roster
    identity = (
        REPO_ROOT / ".github" / "fleet-pr-cap" / "shared_scripts" / "agent_identity.py"
    ).read_text(encoding="utf-8")
    assert '"gaai"' not in identity
