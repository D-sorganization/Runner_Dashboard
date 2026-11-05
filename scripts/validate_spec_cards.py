#!/usr/bin/env python3
"""Lane spec card templates and validator (#3933 S1).

A lane spec card is one of three standard planning documents a lane writes
before implementing an issue: ``requirements.md``, ``design.md`` and
``tasks.md``. Templates live in ``docs/lanes/templates/``; a lane copies
them into its own directory and fills them in. This validates a lane's
filled-in cards against the same structural schema as the templates,
rejecting unedited placeholders and empty task lists.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "docs" / "lanes" / "templates"

PLACEHOLDER_PATTERN = re.compile(r"<[^>\n]+>")

CARD_SCHEMAS: dict[str, tuple[re.Pattern[str], ...]] = {
    "requirements": (
        re.compile(r"^##\s+Problem\s*$", re.MULTILINE),
        re.compile(r"^##\s+Acceptance criteria\s*$", re.MULTILINE),
    ),
    "design": (
        re.compile(r"^##\s+Approach\s*$", re.MULTILINE),
        re.compile(r"^##\s+Alternatives considered\s*$", re.MULTILINE),
    ),
    "tasks": (re.compile(r"^##\s+Steps\s*$", re.MULTILINE),),
}

STEP_LINE = re.compile(r"^\s*-\s*\[[ xX]\]\s*\S", re.MULTILINE)


@dataclass(frozen=True)
class SpecCardFinding:
    """A single validation finding against a spec card document."""

    card: str
    kind: str
    message: str
    remediation: str


def _validate_card_content(card: str, content: str, is_template: bool) -> list[SpecCardFinding]:
    findings: list[SpecCardFinding] = []

    for heading in CARD_SCHEMAS[card]:
        if not heading.search(content):
            findings.append(
                SpecCardFinding(
                    card=card,
                    kind="missing_section",
                    message=f"'{card}.md' is missing a required section.",
                    remediation=f"Copy the section back in from docs/lanes/templates/{card}.md.",
                )
            )

    if is_template:
        return findings

    for match in PLACEHOLDER_PATTERN.finditer(content):
        findings.append(
            SpecCardFinding(
                card=card,
                kind="placeholder",
                message=f"Unedited template placeholder: {match.group(0)}",
                remediation=f"Replace '{match.group(0)}' in {card}.md with real content.",
            )
        )

    if card == "tasks" and not findings:
        steps_match = re.search(r"^##\s+Steps\s*\n(.*)", content, re.DOTALL | re.MULTILINE)
        steps_text = steps_match.group(1) if steps_match else ""
        if not STEP_LINE.search(steps_text):
            findings.append(
                SpecCardFinding(
                    card="tasks",
                    kind="empty_steps",
                    message="'tasks.md' Steps section has no checkbox items.",
                    remediation="Add at least one '- [ ] <step>' line under '## Steps'.",
                )
            )

    return findings


def validate_templates() -> list[SpecCardFinding]:
    """Validate the three shipped templates against their own schema."""
    findings: list[SpecCardFinding] = []
    for card in CARD_SCHEMAS:
        path = TEMPLATES_DIR / f"{card}.md"
        content = path.read_text(encoding="utf-8")
        findings.extend(_validate_card_content(card, content, is_template=True))
    return findings


def validate_lane_dir(lane_dir: Path) -> list[SpecCardFinding]:
    """Validate a lane directory's filled-in requirements/design/tasks cards."""
    findings: list[SpecCardFinding] = []
    for card in CARD_SCHEMAS:
        path = lane_dir / f"{card}.md"
        if not path.is_file():
            findings.append(
                SpecCardFinding(
                    card=card,
                    kind="missing_card",
                    message=f"Lane directory is missing '{card}.md'.",
                    remediation=f"Copy docs/lanes/templates/{card}.md into the lane directory and fill it in.",
                )
            )
            continue
        content = path.read_text(encoding="utf-8")
        findings.extend(_validate_card_content(card, content, is_template=False))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lane-dir",
        type=Path,
        required=True,
        help="Path to the lane directory containing requirements.md, design.md and tasks.md",
    )
    args = parser.parse_args(argv)

    findings = validate_lane_dir(args.lane_dir)
    if not findings:
        print("Spec card validation passed.")
        return 0

    print("ERROR: lane spec cards")
    for finding in findings:
        print(f"  - {finding.card}.md [{finding.kind}]: {finding.message}")
        print(f"    Remediation: {finding.remediation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
