"""Tests for lane spec card templates and validator (#3933 S1).

Spec cards are the three standard lane-planning documents a lane writes
before implementing an issue: ``requirements.md``, ``design.md`` and
``tasks.md``. The validator checks that a lane directory has all three and
that each has its required headings filled in (no unedited template
placeholders).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_spec_cards import (
    TEMPLATES_DIR,
    SpecCardFinding,
    validate_lane_dir,
    validate_templates,
)

REQUIREMENTS_OK = """# Requirements

## Problem
The scheduler drops jobs silently when storage is full.

## Acceptance criteria
- Jobs queued while storage is full are retried, not dropped.
- A failing test demonstrates the drop before the fix.
"""

DESIGN_OK = """# Design

## Approach
Add a bounded retry queue in the scheduler module.

## Alternatives considered
Dropping jobs with a warning log was rejected: it loses work silently.
"""

TASKS_OK = """# Tasks

## Steps
- [ ] Add failing test for dropped jobs under full storage.
- [ ] Implement bounded retry queue.
- [ ] Update docs.
"""


def _write(lane_dir: Path, name: str, content: str) -> None:
    (lane_dir / name).write_text(content, encoding="utf-8")


def test_templates_are_present_and_self_valid() -> None:
    """The three shipped templates exist and validate against their own schema."""
    for name in ("requirements.md", "design.md", "tasks.md"):
        path = TEMPLATES_DIR / name
        assert path.is_file(), f"missing template {path}"

    findings = validate_templates()
    assert findings == []


def test_lane_dir_missing_all_cards_reports_each(tmp_path: Path) -> None:
    findings = validate_lane_dir(tmp_path)
    kinds = {f.kind for f in findings}
    assert kinds == {"missing_card"}
    assert {f.card for f in findings} == {"requirements", "design", "tasks"}


def test_lane_dir_with_valid_cards_passes(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.md", REQUIREMENTS_OK)
    _write(tmp_path, "design.md", DESIGN_OK)
    _write(tmp_path, "tasks.md", TASKS_OK)

    assert validate_lane_dir(tmp_path) == []


def test_lane_dir_missing_section_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.md", "# Requirements\n\nNo sections here.\n")
    _write(tmp_path, "design.md", DESIGN_OK)
    _write(tmp_path, "tasks.md", TASKS_OK)

    findings = validate_lane_dir(tmp_path)
    assert len(findings) == 2
    assert {f.kind for f in findings} == {"missing_section"}
    assert {f.card for f in findings} == {"requirements"}


def test_unedited_placeholder_is_reported(tmp_path: Path) -> None:
    requirements_with_placeholder = REQUIREMENTS_OK.replace(
        "The scheduler drops jobs silently when storage is full.",
        "<describe the problem>",
    )
    _write(tmp_path, "requirements.md", requirements_with_placeholder)
    _write(tmp_path, "design.md", DESIGN_OK)
    _write(tmp_path, "tasks.md", TASKS_OK)

    findings = validate_lane_dir(tmp_path)
    assert len(findings) == 1
    assert findings[0].kind == "placeholder"
    assert findings[0].card == "requirements"


def test_empty_tasks_steps_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.md", REQUIREMENTS_OK)
    _write(tmp_path, "design.md", DESIGN_OK)
    _write(tmp_path, "tasks.md", "# Tasks\n\n## Steps\n")

    findings = validate_lane_dir(tmp_path)
    assert len(findings) == 1
    assert findings[0].kind == "empty_steps"
    assert findings[0].card == "tasks"


def test_finding_is_frozen_dataclass() -> None:
    finding = SpecCardFinding(card="tasks", kind="empty_steps", message="x", remediation="y")
    with pytest.raises(AttributeError):
        finding.kind = "other"  # type: ignore[misc]


def test_cli_exits_nonzero_on_findings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.validate_spec_cards import main

    exit_code = main(["--lane-dir", str(tmp_path)])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "missing_card" in out


def test_cli_exits_zero_when_valid(tmp_path: Path) -> None:
    from scripts.validate_spec_cards import main

    _write(tmp_path, "requirements.md", REQUIREMENTS_OK)
    _write(tmp_path, "design.md", DESIGN_OK)
    _write(tmp_path, "tasks.md", TASKS_OK)

    assert main(["--lane-dir", str(tmp_path)]) == 0
