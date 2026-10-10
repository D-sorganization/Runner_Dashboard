"""RM-8 context diet for Runner_Dashboard (#1870).

AGENTS.md is the single source of agent guidance; CLAUDE.md is a stub
that imports it with Claude Code's `@AGENTS.md` syntax. Long-form repository
guidance lives in `docs/agents/repository-guide.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_MD = REPO_ROOT / "AGENTS.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
REPO_GUIDE = REPO_ROOT / "docs" / "agents" / "repository-guide.md"

TOKEN_BUDGET = 6000
BYTES_PER_TOKEN = 4

CLAUDE_IMPORT_LINE = "@AGENTS.md"
CLAUDE_STUB = (
    "# CLAUDE.md\n"
    "@AGENTS.md\n"
    "<!-- Claude Code reads CLAUDE.md; the line above imports AGENTS.md, "
    "the single source of agent guidance. Edit AGENTS.md (or its fleet-rules sources), "
    "not this file. -->\n"
)


def _heading_to_slug(heading: str) -> str:
    """GitHub-flavoured markdown heading slugifier."""
    text = heading.strip().lower()
    # Replace em-dashes and en-dashes with hyphens
    text = text.replace("—", "-").replace("–", "-")
    # Remove punctuation characters (except hyphens and spaces)
    text = re.sub(r"[^\w\s-]", "", text)
    # Replace whitespace sequences with hyphens
    text = re.sub(r"\s+", "-", text)
    return text


def test_agent_surfaces_under_token_budget() -> None:
    """AGENTS.md + CLAUDE.md must be under 6k tokens (bytes / 4)."""
    total_bytes = len(AGENTS_MD.read_bytes()) + len(CLAUDE_MD.read_bytes())
    token_count = total_bytes / BYTES_PER_TOKEN
    assert token_count < TOKEN_BUDGET, f"Token count {token_count:.1f} exceeds budget of {TOKEN_BUDGET} tokens"


def test_claude_md_is_exact_stub() -> None:
    """CLAUDE.md must be the exact import stub without fleet sections or notes."""
    content = CLAUDE_MD.read_text(encoding="utf-8")
    assert CLAUDE_IMPORT_LINE in [line.strip() for line in content.splitlines()]
    assert "<!-- BEGIN FLEET-MANAGED" not in content

    non_empty = [line.strip() for line in content.splitlines() if line.strip()]
    assert non_empty[0] == "# CLAUDE.md"
    assert non_empty[1] == CLAUDE_IMPORT_LINE
    assert non_empty[2].startswith("<!-- Claude Code reads CLAUDE.md")
    assert len(non_empty) == 3


def test_agents_md_opens_with_repo_quick_reference() -> None:
    """AGENTS.md must lead with the short repo quick reference."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    first_section = text.split("\n## ", 2)[1]
    assert first_section.startswith("Quick Reference")

    required_snippets = [
        "start-dashboard.sh",
        "stop-dashboard.sh",
        "pytest tests/ -q",
        "ruff check backend/",
        "mypy backend/",
        "npm",
        "spec-exempt",
        "--head",
    ]
    for snippet in required_snippets:
        assert snippet in first_section, f"Missing {snippet!r} in Quick Reference"

    # Ensure Quick Reference precedes any fleet-managed block
    assert text.index("Quick Reference") < text.index("BEGIN FLEET-MANAGED")


def test_repository_guide_exists_and_contains_relocated_sections() -> None:
    """docs/agents/repository-guide.md holds all long-form guidance."""
    assert REPO_GUIDE.is_file(), "docs/agents/repository-guide.md not found"
    guide_text = REPO_GUIDE.read_text(encoding="utf-8")

    expected_sections = [
        "Sibling repos & boundaries",
        "Multi-Agent Coordination",
        "Issue Taxonomy and Dispatch",
        "Project Overview",
        "Architecture",
        "Dev Commands",
        "CI/CD",
        "Coding Conventions",
        "Engineering principles (mandatory)",
        "Safety & Security",
        "Detailed Python Coding Standards",
        "Detailed Web Development Standards",
        "C++ Coding Standards",
        "Closing issues",
    ]
    for section in expected_sections:
        assert section in guide_text, f"Missing {section!r} in repository guide"


def test_all_links_in_agents_md_resolve() -> None:
    """Every internal link in AGENTS.md must resolve to a valid file and anchor."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    # Match markdown links: [label](target)
    links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text)

    # Collect headings from repository guide for anchor validation
    guide_lines = REPO_GUIDE.read_text(encoding="utf-8").splitlines()
    guide_slugs = {_heading_to_slug(line.lstrip("#").strip()) for line in guide_lines if line.startswith("#")}

    for label, target in links:
        if target.startswith("http://") or target.startswith("https://"):
            continue  # external link

        file_part, _, anchor_part = target.partition("#")
        target_path = (REPO_ROOT / file_part).resolve()
        assert target_path.exists(), f"Link [{label}]({target}) in AGENTS.md points to missing file: {file_part}"

        if anchor_part and target_path == REPO_GUIDE.resolve():
            # Check anchor slug
            assert anchor_part in guide_slugs, (
                f"Anchor #{anchor_part} from link [{label}]({target}) not found in {REPO_GUIDE.name}"
            )


def test_claude_import_loads_agents_md() -> None:
    """Simulates Claude Code session loading AGENTS.md through @AGENTS.md import."""
    claude_text = CLAUDE_MD.read_text(encoding="utf-8")
    lines = [line.strip() for line in claude_text.splitlines()]

    import_lines = [line for line in lines if line.startswith("@")]
    assert import_lines == ["@AGENTS.md"]

    imported_file = REPO_ROOT / import_lines[0].lstrip("@")
    assert imported_file.is_file()
    imported_text = imported_file.read_text(encoding="utf-8")
    assert "Runner_Dashboard" in imported_text
    assert "Quick Reference" in imported_text
    assert "BEGIN FLEET-MANAGED: reasoning-engagement" in imported_text
