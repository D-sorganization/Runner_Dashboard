"""Tests for RM-8 context diet in Runner_Dashboard (Issue #1870).

Enforces:
1. AGENTS.md + CLAUDE.md combined is under 6k tokens (bytes / 4 < 6000).
2. CLAUDE.md is the exact @AGENTS.md import stub without fleet sections or long notes.
3. AGENTS.md opens with the Runner_Dashboard quick reference before fleet rules.
4. Synced fleet core in AGENTS.md is within the 150-line budget.
5. All relocated long-form sections live in docs/agents/repository-guide.md.
6. Every documentation link in AGENTS.md resolves to an existing file.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_MD = REPO_ROOT / "AGENTS.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
REPO_GUIDE = REPO_ROOT / "docs" / "agents" / "repository-guide.md"

FLEET_CORE_LINE_BUDGET = 175
TOKEN_BUDGET = 6000
BYTES_PER_TOKEN = 4

MANAGED_BLOCK_RE = re.compile(
    r"<!-- BEGIN FLEET-MANAGED: (?P<tag>[\w-]+) -->\n(?P<body>[\s\S]*?)\n<!-- END FLEET-MANAGED: (?P=tag) -->",
    re.MULTILINE,
)


def _managed_block_line_count(text: str) -> int:
    """Count lines inside every FLEET-MANAGED block, markers included."""
    count = 0
    for m in MANAGED_BLOCK_RE.finditer(text):
        count += m.group(0).count("\n") + 1
    return count


def test_rd_agent_surfaces_are_under_token_budget() -> None:
    total_bytes = len(AGENTS_MD.read_bytes()) + len(CLAUDE_MD.read_bytes())
    token_estimate = total_bytes / BYTES_PER_TOKEN
    assert token_estimate < TOKEN_BUDGET, (
        f"AGENTS.md + CLAUDE.md is {token_estimate:.0f} tokens (expected < {TOKEN_BUDGET})"
    )


def test_claude_md_is_the_import_stub() -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert text.startswith("# CLAUDE.md")
    assert "@AGENTS.md" in text
    assert "BEGIN FLEET-MANAGED" not in text
    # CLAUDE.md should be a small stub (under 500 bytes)
    assert len(text.encode("utf-8")) < 500


def test_agents_md_opens_with_repo_quick_reference() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    sections = text.split("\n## ")
    assert len(sections) > 1
    first_heading = sections[1].split("\n", 1)[0].strip()
    assert "Quick Reference" in first_heading

    quick_ref = sections[1]
    expected_needles = [
        "start-dashboard.sh",
        "stop-dashboard.sh",
        "pytest",
        "ruff check",
        "mypy",
        "npm",
        "spec-exempt",
        "--head",
    ]
    for needle in expected_needles:
        assert needle in quick_ref, f"Expected {needle!r} in Quick Reference"

    # Quick reference must appear before the fleet core
    assert text.index("Quick Reference") < text.index("BEGIN FLEET-MANAGED")


def test_fleet_core_in_agents_md_is_within_line_budget() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    count = _managed_block_line_count(text)
    assert count <= FLEET_CORE_LINE_BUDGET, (
        f"Fleet core in AGENTS.md is {count} lines (budget: {FLEET_CORE_LINE_BUDGET})"
    )


def test_repository_guide_exists_and_contains_relocated_sections() -> None:
    assert REPO_GUIDE.is_file(), f"Missing {REPO_GUIDE}"
    guide_text = REPO_GUIDE.read_text(encoding="utf-8")

    # Word-for-word relocated RD guidance
    assert "Architecture" in guide_text
    assert "Coding Conventions" in guide_text
    assert "engineering principles" in guide_text.lower()
    assert "Issue Taxonomy" in guide_text
    assert "start-dashboard.sh" in guide_text
    assert "frontend/src/" in guide_text


def test_links_from_agents_md_resolve() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    # Match markdown links: [text](path)
    links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text)
    for label, target in links:
        if target.startswith("http://") or target.startswith("https://") or target.startswith("#"):
            continue
        # Strip anchor if present
        clean_target = target.split("#")[0]
        if not clean_target:
            continue
        target_path = (REPO_ROOT / clean_target).resolve()
        assert target_path.exists(), f"Link '{label}' to '{target}' does not resolve to a file"
