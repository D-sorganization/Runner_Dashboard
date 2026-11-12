"""User-facing docs must not advertise GAAI as a live remediation lane (RM #2726, epic #2720).

GAAI is quarantined: the Runner Dashboard has no live GAAI provider, and the roster keeps
``gaai`` only as a ``phased_out`` identity for attribution history (child #7). These checks
cover the README, the SPEC and the C4 map. The roster and priority lists are not scanned.
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


def test_roster_keeps_gaai_as_phased_out_identity() -> None:
    roster = (REPO_ROOT / "backend" / "coordination" / "roster.py").read_text(encoding="utf-8")
    assert '"gaai"' in roster, "child #7 owns dropping gaai; this scrub must keep it"
