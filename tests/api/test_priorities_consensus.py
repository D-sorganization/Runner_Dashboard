"""Board-meeting consensus parser (issue #1227, epic #1192).

The fixture is RM ``docs/templates/board-consensus.md`` filled in, keeping the
template's malformed table separators (wrong column counts) and adding a
row with an extra cell, so the parser is proven tolerant of both.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from priorities.consensus import parse_consensus, parse_table

FIXTURE = Path(__file__).parent / "fixtures" / "board_meeting" / "consensus.md.txt"


@pytest.fixture(scope="module")
def consensus():
    return parse_consensus(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_metadata_survives_malformed_separator(consensus) -> None:
    meta = consensus.metadata
    assert meta.date == "2026-09-21"
    assert meta.quorum == "4/4 seats present"
    assert meta.instruction_writer == "Alpha"
    assert meta.consensus_reached == "partial"


@pytest.mark.unit
def test_active_priorities_are_ranked_with_sub_bullets(consensus) -> None:
    active = consensus.active
    assert [a.rank for a in active] == [1, 2, 3]
    first = active[0]
    assert first.item == "Fleet coordination API"
    assert first.project == "Runner_Dashboard"
    assert first.scope == "one API for priorities, presence and claims"
    assert first.assigned_to == "claude"
    assert first.tracking == "#1192"
    assert first.acceptance == "briefing endpoint answers in one call"


@pytest.mark.unit
def test_scope_may_contain_plain_hyphens(consensus) -> None:
    second = consensus.active[1]
    assert second.project == "Tools_Private"
    assert second.scope == "sector solver - r-theta-z"
    assert second.assigned_to == "Dispatcher will assign"


@pytest.mark.unit
def test_sub_bullets_without_blank_line_are_attached(consensus) -> None:
    third = consensus.active[2]
    assert third.project == "Design-Procedures"
    assert third.tracking == "to be filed"
    assert third.assigned_to == "codex"


@pytest.mark.unit
def test_deferred_backlog_tolerates_extra_cells(consensus) -> None:
    deferred = consensus.deferred
    assert [d.item for d in deferred] == ["Bunkershot inversion", "Historian retention"]
    assert deferred[0].reason == "blocked on F0 frame"
    assert deferred[1].reassess == "2026-10-12"


@pytest.mark.unit
def test_borda_rows_skip_ellipsis_and_parse_scores(consensus) -> None:
    borda = consensus.borda
    assert [(b.rank, b.item, b.score) for b in borda] == [
        (1, "Fleet coordination API", 14),
        (2, "DC return electrode", 11),
        (3, "Ash fusion review", 5),
    ]
    assert borda[0].votes == "Alpha(1), Bravo(2), Charlie(1), Delta(3)"


@pytest.mark.unit
def test_disagreements_skip_template_placeholders(consensus) -> None:
    assert len(consensus.disagreements) == 1
    flag = consensus.disagreements[0]
    assert "Bravo" in flag
    assert "Fleet coordination API" in flag
    assert "customer date" in flag


@pytest.mark.unit
def test_unfilled_template_yields_no_real_items() -> None:
    text = (
        "## Consensus Priority List\n\n### Active Priorities (Execute This Cycle)\n\n"
        "1. **`<item>`** — `<project>` — `<one-line-scope>`\n   - Assigned to: `<agent>`\n\n"
        "### Deferred Backlog (Documented, Not Executed)\n\n| Item | Project | Reason for Deferral | Reassess Date |\n"
        "| --- | --- | --- | --- |\n| `<item>` | `<project>` | `<reason>` | `YYYY-MM-DD` |\n"
    )
    parsed = parse_consensus(text)
    assert parsed.active == []
    assert parsed.deferred == []


@pytest.mark.unit
def test_empty_document_parses_to_empty_consensus() -> None:
    parsed = parse_consensus("")
    assert parsed.active == [] and parsed.deferred == [] and parsed.borda == [] and parsed.disagreements == []
    assert parsed.metadata.date == ""


@pytest.mark.unit
def test_parse_table_pads_short_rows_and_ignores_separators() -> None:
    rows = parse_table(["| A | B | C |", "| --- | :-: |", "| 1 |", "| x | y | z | w |"])
    assert rows == [{"a": "1", "b": "", "c": ""}, {"a": "x", "b": "y", "c": "z"}]
