"""Tests for the slice-7 cutover docs (#3085, public-repo slice 7, epic #3077).

Acceptance criteria covered:
- The checklist doc and the epic checklist match (hardcoded snapshot of the
  epic #3077 issue body's own "Owner checklist" section -- there is no
  network access in tests, so the epic body can't be fetched live; this is
  the same "doc test" pattern as tests/test_governance_docs.py).
- Runbooks name owner-only steps explicitly; nothing in them is automated
  against GitHub visibility.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNBOOKS = REPO_ROOT / "docs" / "runbooks"
CHECKLIST = RUNBOOKS / "public-repo-owner-cutover-checklist.md"
ROLLBACK = RUNBOOKS / "public-repo-rollback.md"
ROTATION = RUNBOOKS / "public-repo-credential-rotation.md"

# Verbatim from epic #3077's "Owner checklist (Dieter; final, after every
# slice is merged and green)" section.
_EPIC_CHECKLIST_ITEMS = [
    "Review the scan reports (tree, history, Actions logs/artifacts, forks, issue/PR discussions) and approve.",
    "Approve the rotation list and rotate those credentials.",
    "Freeze the private repo: confirm zero open PRs and set it read-only.",
    "Create the private `Runner_Dashboard-overlay` repo.",
    "Rename this repo to `Runner_Dashboard-private` and archive it; create "
    "the fresh public `Runner_Dashboard` repo and run the bootstrap in "
    "apply mode; confirm the merge queue, rulesets, required checks and "
    "re-filed holds, including the HOLD #2657 equivalent.",
    "Approve the runner-group scoping for the heavy self-hosted jobs.",
    "Place the private overlay on each host and set `RD_OVERLAY_PATH`.",
    "Confirm the rollback steps and who runs the recurring scan.",
    "Flip visibility to public (Dieter's action only).",
]


def _read(path: Path) -> str:
    assert path.is_file(), f"missing doc: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def _checkbox_items(text: str) -> list[str]:
    matches = re.finditer(r"- \[ \] (.+?)(?=\n- \[ \]|\n##|\Z)", text, re.DOTALL)
    return [" ".join(match.group(1).split()) for match in matches]


def test_checklist_doc_matches_epic_checklist_exactly() -> None:
    doc_items = _checkbox_items(_read(CHECKLIST))
    expected = [" ".join(item.split()) for item in _EPIC_CHECKLIST_ITEMS]
    assert doc_items == expected
    assert len(doc_items) >= 9


def test_rollback_runbook_is_owner_only_with_fifteen_minute_rto() -> None:
    text = _read(ROLLBACK).lower()
    assert "15 minutes" in text
    assert "owner-only" in text
    assert "nothing here runs" in text or "nothing in them is automated" in text
    assert "gh repo edit" in text
    assert "--visibility private" in text


def test_rotation_runbook_is_owner_only_and_list_driven() -> None:
    text = _read(ROTATION).lower()
    assert "owner-only" in text
    assert "rotation list" in text
    assert "scan_history.py" in text
    assert "never" in text
    assert "credential value" in text
