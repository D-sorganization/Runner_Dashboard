"""Regression: one canonical merge-shepherd hold label set (#4573)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from coordination.merge_shepherd import HOLD_LABELS, is_pr_held
from coordination.merge_shepherd_holds import MERGE_SHEPHERD_HOLD_LABELS, MergeShepherdHoldLabel
from coordination.merge_shepherd_low_risk import LOW_RISK_BLOCKED_LABELS
from coordination.merge_shepherd_pass import SHEPHERD_HOLD_LABELS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL = _REPO_ROOT / "backend" / "coordination" / "merge_shepherd_holds.py"
_SCAN_ROOTS = (
    _REPO_ROOT / "backend" / "coordination",
    _REPO_ROOT / "backend" / "staff" / "review_arm",
)
_HOLD_TOKENS = frozenset(member.value for member in MergeShepherdHoldLabel)
_HOLD_NAME_MARKERS = frozenset(
    {
        "hold",
        "hold_label",
        "hold_labels",
        "blocked_label",
        "shepherd_hold",
    }
)


def _string_constants(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        out: set[str] = set()
        for elt in node.elts:
            out.update(_string_constants(elt))
        return out
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {"frozenset", "set", "tuple", "list"}:
            out: set[str] = set()
            for arg in node.args:
                out.update(_string_constants(arg))
            return out
    return set()


def _hold_literals_in_assignments(tree: ast.AST) -> list[tuple[str, set[str]]]:
    found: list[tuple[str, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        target_names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        literal_strings = _string_constants(node.value)
        hold_hits = literal_strings & _HOLD_TOKENS
        if not hold_hits:
            continue
        if any("HOLD" in name.upper() for name in target_names):
            found.append((",".join(target_names) or "?", hold_hits))
            continue
        if any(marker in name.lower() for name in target_names for marker in _HOLD_NAME_MARKERS):
            found.append((",".join(target_names) or "?", hold_hits))
    return found


def _forbidden_hold_frozenset_assignments(tree: ast.AST) -> list[str]:
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id not in {"HOLD_LABELS", "SHEPHERD_HOLD_LABELS", "LOW_RISK_BLOCKED_LABELS"}:
                continue
            if isinstance(node.value, ast.Call):
                bad.append(target.id)
    return bad


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
def test_no_inline_hold_label_literals_outside_canonical_module() -> None:
    """Any hold-label string literal outside merge_shepherd_holds.py must fail."""
    violations: list[str] = []
    for root in _SCAN_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == _CANONICAL.resolve():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for name, hits in _hold_literals_in_assignments(tree):
                violations.append(f"{path.relative_to(_REPO_ROOT)} ({name}): {sorted(hits)}")
    assert not violations, "Hold literals outside enum:\n" + "\n".join(violations)


@pytest.mark.unit
def test_legacy_modules_do_not_define_inline_hold_frozensets() -> None:
    for path in (
        _REPO_ROOT / "backend" / "coordination" / "merge_shepherd.py",
        _REPO_ROOT / "backend" / "coordination" / "merge_shepherd_pass.py",
        _REPO_ROOT / "backend" / "coordination" / "jobs.py",
        _REPO_ROOT / "backend" / "coordination" / "merge_shepherd_low_risk.py",
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert _forbidden_hold_frozenset_assignments(tree) == []
