"""CI DRY & anchor linter for versioned playbooks against the Barb Operating Manual (#3486)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from staff.playbooks.schema import (
    DEPRECATED_CLAIM_ENDPOINTS,
    BarbOperatingManual,
    PlaybookDefinition,
    PlaybookValidationError,
    normalize_anchor,
)

_INLINE_ANCHOR_RE = re.compile(r"\bmanual#([a-z0-9]+(?:-[a-z0-9]+)*)\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class PlaybookLintIssue:
    playbook_id: str
    code: str
    message: str


def _normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.strip()).lower()


def _extract_policy_clauses(text: str, *, min_chars: int) -> tuple[str, ...]:
    clauses = [
        _normalize_text(s.strip(" .;:"))
        for s in _SENTENCE_SPLIT_RE.split(text)
        if len(_normalize_text(s.strip(" .;:"))) >= min_chars
    ]
    full_norm = _normalize_text(text.strip(" .;:"))
    if len(full_norm) >= min_chars and full_norm not in clauses:
        clauses.append(full_norm)
    return tuple(clauses)


def lint_playbook_against_manual(
    playbook: PlaybookDefinition, manual: BarbOperatingManual, *, min_copied_policy_chars: int = 40
) -> tuple[PlaybookLintIssue, ...]:
    """Lint a playbook for broken manual anchors, copied policy prose, and retired claim paths."""
    issues: list[PlaybookLintIssue] = []
    valid_anchors = {section.anchor for section in manual.sections}
    for raw_anchor in playbook.manual_anchors:
        anchor = normalize_anchor(raw_anchor)
        if anchor not in valid_anchors:
            issues.append(PlaybookLintIssue(playbook.playbook_id, "BROKEN_ANCHOR", f"Missing anchor {anchor!r}."))

    combined_texts = [playbook.summary, *playbook.steps]
    for text_item in combined_texts:
        for match in _INLINE_ANCHOR_RE.finditer(text_item):
            if match.group(1) not in valid_anchors:
                issues.append(
                    PlaybookLintIssue(playbook.playbook_id, "BROKEN_ANCHOR", f"Missing inline manual#{match.group(1)}.")
                )

    corpus = _normalize_text(" ".join(combined_texts))
    for deprecated in sorted(DEPRECATED_CLAIM_ENDPOINTS):
        if deprecated.lower() in corpus:
            issues.append(
                PlaybookLintIssue(playbook.playbook_id, "DEPRECATED_CLAIM_ENDPOINT", f"Deprecated {deprecated!r}.")
            )
            break

    for section in manual.sections:
        if any(c in corpus for c in _extract_policy_clauses(section.body, min_chars=min_copied_policy_chars)):
            issues.append(
                PlaybookLintIssue(playbook.playbook_id, "COPIED_POLICY_TEXT", f"Copied policy from #{section.anchor}.")
            )

    return tuple(issues)


def assert_playbooks_clean(
    playbooks: Iterable[PlaybookDefinition], manual: BarbOperatingManual, *, min_copied_policy_chars: int = 40
) -> None:
    """Raise ``PlaybookValidationError`` if any playbook fails anchor or DRY policy linting."""
    all_issues = [
        issue
        for pb in playbooks
        for issue in lint_playbook_against_manual(pb, manual, min_copied_policy_chars=min_copied_policy_chars)
    ]
    if all_issues:
        raise PlaybookValidationError(
            "Playbook lint failed: " + "; ".join(f"[{i.code}] {i.message}" for i in all_issues)
        )
