"""Docs/tests-only low-risk merge shepherd arming (no approving review required)."""

from __future__ import annotations

import os
import posixpath
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_LOW_RISK_LINE_CAP = 300

LOW_RISK_BLOCKED_LABELS = frozenset({"do-not-merge", "hold", "needs-dieter"})

_AGENT_INSTRUCTION_FILENAMES = frozenset({"AGENTS.md", "CLAUDE.md", "GEMINI.md", "SKILL.md"})
_AGENT_INSTRUCTION_PREFIXES = (
    ".cursor/",
    ".claude/",
    ".agents/",
    "fleet-rules/",
)
_TEST_BEHAVIOUR_FILENAMES = frozenset({"conftest.py", "pytest.ini", "pyproject.toml"})


def _normalize_changed_path(path: str) -> str:
    raw = path.replace("\\", "/").lstrip("./")
    return posixpath.normpath(raw)


def _path_has_dotdot(path: str) -> bool:
    parts = path.replace("\\", "/").lstrip("./").split("/")
    return ".." in parts


def _is_agent_instruction_path(normalized: str) -> bool:
    name = posixpath.basename(normalized)
    if name in _AGENT_INSTRUCTION_FILENAMES:
        return True
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in _AGENT_INSTRUCTION_PREFIXES
    )


def _is_test_behaviour_path(normalized: str) -> bool:
    name = posixpath.basename(normalized)
    if name in _TEST_BEHAVIOUR_FILENAMES:
        return True
    if name == "conftest.py":
        return True
    if normalized.startswith(".github/"):
        return True
    if normalized.startswith("tests/") and "/fixtures/" in f"/{normalized}/":
        return True
    return False


def is_docs_or_tests_path(path: str) -> bool:
    """True when a changed file is confined to docs/tests surfaces only."""
    if _path_has_dotdot(path):
        return False
    normalized = _normalize_changed_path(path)
    if not normalized or normalized in {".", ".."}:
        return False
    if _is_agent_instruction_path(normalized):
        return False
    if _is_test_behaviour_path(normalized):
        return False

    name = posixpath.basename(normalized)
    if name.endswith((".md", ".qmd", ".rst")):
        return True
    if normalized.startswith("docs/"):
        return True
    if normalized.startswith("changes/"):
        return True
    if normalized.startswith("tests/"):
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    return bool(name.endswith("_test.py"))


@dataclass(frozen=True)
class LowRiskClassification:
    eligible: bool
    detail: str


def classify_docs_tests_only_arm(
    *,
    file_paths: Sequence[str],
    additions: int,
    deletions: int,
    line_cap: int,
    labels: Sequence[str],
    checks_state: str,
    files_complete: bool,
) -> LowRiskClassification:
    """Return whether a PR may arm without an approving review (docs/tests-only path)."""
    if str(checks_state).upper() != "SUCCESS":
        return LowRiskClassification(False, "required_checks_not_green")
    if any(str(lbl).strip().casefold() in LOW_RISK_BLOCKED_LABELS for lbl in labels):
        return LowRiskClassification(False, "blocked_label")
    if not files_complete:
        return LowRiskClassification(False, "files_incomplete")
    if not file_paths:
        return LowRiskClassification(False, "no_changed_files")
    changed_lines = int(additions) + int(deletions)
    if changed_lines > line_cap:
        return LowRiskClassification(False, "over_line_cap")
    disallowed = [p for p in file_paths if not is_docs_or_tests_path(p)]
    if disallowed:
        return LowRiskClassification(False, f"mixed_paths:{disallowed[0]}")
    return LowRiskClassification(True, "docs_tests_only")


def low_risk_line_cap() -> int:
    raw = os.environ.get("MERGE_SHEPHERD_LOW_RISK_LINE_CAP", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_LOW_RISK_LINE_CAP


def arm_eligibility_reason(
    *,
    review_decision: str | None,
    file_paths: Sequence[str],
    additions: int,
    deletions: int,
    line_cap: int,
    labels: Sequence[str],
    checks_state: str,
    files_complete: bool,
) -> tuple[str, str]:
    """Return (eligibility, detail): reviewed | docs_tests_only | no_approval_*."""
    if str(review_decision or "").upper() == "APPROVED":
        return "reviewed", "review_decision_approved"
    low = classify_docs_tests_only_arm(
        file_paths=file_paths,
        additions=additions,
        deletions=deletions,
        line_cap=line_cap,
        labels=labels,
        checks_state=checks_state,
        files_complete=files_complete,
    )
    if low.eligible:
        return "docs_tests_only", low.detail
    return "no_approval", low.detail
