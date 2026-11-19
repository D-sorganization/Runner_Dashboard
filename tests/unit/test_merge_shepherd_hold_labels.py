"""Regression: one canonical merge-shepherd hold label set (#4573)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from coordination.merge_shepherd import HOLD_LABELS, is_pr_held
from coordination.merge_shepherd_holds import MergeShepherdHoldLabel, MERGE_SHEPHERD_HOLD_LABELS
from coordination.merge_shepherd_low_risk import LOW_RISK_BLOCKED_LABELS
from coordination.merge_shepherd_pass import SHEPHERD_HOLD_LABELS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COORDINATION = _REPO_ROOT / "backend" / "coordination"

_FORBIDDEN_LOCAL_HOLD_DEFS = {
    _COORDINATION / "merge_shepherd.py",
    _COORDINATION / "merge_shepherd_pass.py",
    _COORDINATION / "jobs.py",
    _COORDINATION / "merge_shepherd_low_risk.py",
}


def _assignment_targets(tree: ast.AST, name: str) -> list[ast.AST]:
    matches: list[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                matches.append(node.value)
    return matches


@pytest.mark.unit
def test_needs_owner_label_blocks_shepherd() -> None:
    assert MergeShepherdHoldLabel.NEEDS_OWNER.value in MERGE_SHEPHERD_HOLD_LABELS
    assert is_pr_held([MergeShepherdHoldLabel.NEEDS_OWNER.value], "")


@pytest.mark.unit
def test_exports_alias_canonical_hold_set() -> None:
    assert HOLD_LABELS is MERGE_SHEPHERD_HOLD_LABELS
    assert SHEPHERD_HOLD_LABELS is MERGE_SHEPHERD_HOLD_LABELS
    assert LOW_RISK_BLOCKED_LABELS <= MERGE_SHEPHERD_HOLD_LABELS


@pytest.mark.unit
def test_merge_shepherd_modules_do_not_define_local_hold_frozensets() -> None:
    """Fail if another inline hold set appears in the four shepherd modules."""
    for path in _FORBIDDEN_LOCAL_HOLD_DEFS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in ("HOLD_LABELS", "SHEPHERD_HOLD_LABELS", "LOW_RISK_BLOCKED_LABELS"):
            for value in _assignment_targets(tree, name):
                assert not isinstance(value, ast.Call), f"{path.name} must not define {name} inline"