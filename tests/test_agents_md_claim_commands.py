"""AGENTS.md claim commands must reference scripts that exist (#4268 H-01)."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_MD = REPO_ROOT / "AGENTS.md"

_CLAIM_SECTION = re.compile(
    r"\*\*Dispatch & Claims\.\*\*(.+?)(?=\n- \*\*|\n## )",
    re.DOTALL,
)
_MODULE_INVOCATION = re.compile(r"python -m scripts\.([a-z_][a-z0-9_]*)")
_SCRIPT_FILE = re.compile(r"python scripts/([A-Za-z0-9_./-]+\.py)")


def _claim_section_text() -> str:
    text = AGENTS_MD.read_text(encoding="utf-8")
    match = _CLAIM_SECTION.search(text)
    assert match is not None, "Dispatch & Claims section missing from AGENTS.md"
    return match.group(1)


def test_agents_md_claim_commands_reference_existing_repo_scripts() -> None:
    section = _claim_section_text()
    assert "scripts.check_agent_claim" not in section, "check_agent_claim lives only in Repository_Management"
    assert "STAFF_RM_ROOT" in section

    for module in _MODULE_INVOCATION.findall(section):
        script_py = REPO_ROOT / "scripts" / f"{module}.py"
        assert script_py.is_file(), f"AGENTS.md references missing scripts.{module} ({script_py})"

    for rel in _SCRIPT_FILE.findall(section):
        path = REPO_ROOT / rel
        assert path.is_file(), f"AGENTS.md references missing script path {rel}"


def test_agents_md_documents_rm_check_agent_claim_separately() -> None:
    section = _claim_section_text()
    assert "check_agent_claim" in section
    assert "STAFF_RM_ROOT" in section
