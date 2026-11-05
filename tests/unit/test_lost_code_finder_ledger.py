"""Contract tests for Lost Code Finder evidence ledger (#3867, TDD)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.lost_code_finder_ledger import (
    INDIRECTION_CHECK_IDS,
    VERDICT_UNKNOWN,
    coerce_verdict_for_indirection,
    validate_finding,
    validate_ledger_document,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _base_finding(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "path": "backend/example.py",
        "symbol": "dead_fn",
        "evidence": "vulture reports 100% confidence unused; no importers in graph.",
        "origin": {"commit": "abc1234", "pr": 100, "issue": 99},
        "inferred_purpose": "Legacy helper before refactor.",
        "replacement": None,
        "verdict": VERDICT_UNKNOWN,
        "confidence": 0.4,
        "links": ["https://github.com/D-sorganization/Runner_Dashboard/commit/abc1234"],
        "indirection_checks_cleared": list(INDIRECTION_CHECK_IDS),
    }
    payload.update(overrides)
    return payload


def test_validate_finding_requires_core_fields() -> None:
    bad = _base_finding()
    del bad["symbol"]
    assert any("symbol" in err for err in validate_finding(bad))


def test_missing_indirection_check_forces_unknown_verdict() -> None:
    cleared = [c for c in INDIRECTION_CHECK_IDS if c != "reexport_shim"]
    finding = _base_finding(verdict="RETIRE", indirection_checks_cleared=cleared, confidence=0.95)
    assert validate_finding(finding)
    coerced = coerce_verdict_for_indirection(finding)
    assert coerced["verdict"] == VERDICT_UNKNOWN
    finding["verdict"] = VERDICT_UNKNOWN
    assert validate_finding(finding) == []


def test_retire_rejected_when_any_indirection_uncleared() -> None:
    finding = _base_finding(
        verdict="RETIRE",
        indirection_checks_cleared=["dynamic_dispatch"],
        confidence=0.9,
    )
    assert any("RETIRE" in err or "UNKNOWN" in err for err in validate_finding(finding))


def test_dispatch_contract_shim_must_not_validate_as_retire() -> None:
    shim_path = _REPO_ROOT / "backend" / "dispatch_contract.py"
    text = shim_path.read_text(encoding="utf-8")
    assert "Compatibility shim" in text
    naive = _base_finding(
        path="backend/dispatch_contract.py",
        symbol="dispatch_contract",
        evidence="Import graph shows no local callers.",
        origin={"commit": "HEAD", "pr": None, "issue": None},
        inferred_purpose="Unknown",
        verdict="RETIRE",
        confidence=0.85,
        indirection_checks_cleared=["dynamic_dispatch", "plugin_registry"],
    )
    assert validate_finding(naive)

    documented = _base_finding(
        path="backend/dispatch_contract.py",
        symbol="dispatch_contract",
        evidence=text.splitlines()[0],
        origin={"commit": "HEAD", "pr": None, "issue": None},
        inferred_purpose="v1→v2 dispatch API compat re-export seam per module docstring.",
        verdict="SAVE",
        confidence=0.9,
        indirection_checks_cleared=list(INDIRECTION_CHECK_IDS),
    )
    assert validate_finding(documented) == []


def test_ledger_document_validates_findings_list() -> None:
    doc = {"repo": "Tools", "findings": [_base_finding()]}
    assert validate_ledger_document(doc) == []
    assert validate_ledger_document({"repo": "Tools", "findings": "nope"})
