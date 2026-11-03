"""Unit tests for Lost Code Finder tier 1/2 scan harness (#3868)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from staff.lost_code_finder_ledger import validate_ledger_document
from staff.lost_code_finder_scan import (
    Tier2Context,
    build_finding_from_candidate,
    cartographer_source_globs,
    parse_vulture_lines,
    run_repo_scan,
    tier1_import_references,
)

pytestmark = pytest.mark.unit


def test_parse_vulture_lines_extracts_path_and_symbol() -> None:
    lines = [
        "backend/dead.py:12: unused function 'orphan_fn' (60% confidence)",
        "backend/ok.py:3: unused import 'os' (90% confidence)",
    ]
    hits = parse_vulture_lines(lines)
    assert hits[0]["path"] == "backend/dead.py" and hits[0]["symbol"] == "orphan_fn"
    assert hits[0]["confidence"] == 0.6


def test_tier1_import_graph_marks_importers(tmp_path: Path) -> None:
    pkg = tmp_path / "backend"
    pkg.mkdir()
    (pkg / "used.py").write_text("from backend.lib import helper\nhelper()\n", encoding="utf-8")
    (pkg / "lib.py").write_text("def helper() -> None:\n    pass\n", encoding="utf-8")
    (pkg / "dead.py").write_text("def orphan() -> None:\n    pass\n", encoding="utf-8")
    refs = tier1_import_references(tmp_path, ("backend/dead.py", "backend/lib.py"))
    assert refs["backend/lib.py"] and "backend/used.py" in refs["backend/lib.py"]
    assert refs["backend/dead.py"] == []


def test_run_repo_scan_writes_valid_ledger_without_tier3(tmp_path: Path) -> None:
    repo_root = tmp_path / "Tools"
    repo_root.mkdir()
    target = repo_root / "backend" / "orphan_module.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        textwrap.dedent(
            """
            def unused_api() -> None:
                pass
            """
        ),
        encoding="utf-8",
    )
    vulture_out = [f"backend/orphan_module.py:2: unused function 'unused_api' (100% confidence)"]
    ledger = run_repo_scan(
        "Tools",
        repo_root,
        tier1_vulture_lines=vulture_out,
        tier2=Tier2Context(head_commit="abc1234", cartographer_hits=[]),
        allow_tier3=False,
    )
    assert ledger["repo"] == "Tools"
    assert validate_ledger_document(ledger) == []
    row = ledger["findings"][0]
    assert row["verdict"] == "UNKNOWN"
    assert "reexport_shim" in row["indirection_checks_cleared"] or row["verdict"] == "UNKNOWN"


def test_cartographer_globs_include_contract_shims() -> None:
    globs = cartographer_source_globs()
    assert any("contract" in g for g in globs)


def test_build_finding_marks_cartographer_hit_as_save_when_checks_cleared() -> None:
    candidate = {
        "path": "backend/dispatch_contract.py",
        "symbol": "dispatch_contract",
        "confidence": 0.5,
        "evidence": "vulture unused",
    }
    finding = build_finding_from_candidate(
        candidate,
        tier2=Tier2Context(
            head_commit="deadbeef",
            cartographer_hits=["backend/dispatch_contract.py"],
            docstring="Compatibility shim — re-exports dispatch API.",
        ),
        import_refs=[],
        clear_all_indirection=True,
    )
    assert finding["verdict"] == "SAVE"
    assert validate_ledger_document({"repo": "Runner_Dashboard", "findings": [finding]}) == []
