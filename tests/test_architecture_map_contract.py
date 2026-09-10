"""Unit tests for the Mermaid C4 Architecture Map Contract validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.architecture_map_contract import (
    ArchitectureMapContractError,
    validate_architecture_map,
)

ROOT = Path(__file__).resolve().parents[1]
C4_PATH = ROOT / "docs" / "architecture" / "C4.md"


def test_validate_architecture_map_real_file() -> None:
    """The canonical docs/architecture/C4.md must satisfy the contract."""
    res = validate_architecture_map(C4_PATH)
    assert res.is_valid is True
    assert res.context_views >= 1
    assert res.container_views >= 1
    assert len(res.feature_map_entries) >= 1
    assert len(res.changelog_entries) >= 1


def test_validate_architecture_map_rejects_missing_file(tmp_path: Path) -> None:
    """Non-existent files must fail validation."""
    non_existent = tmp_path / "C4.md"
    with pytest.raises(ArchitectureMapContractError, match="does not exist"):
        validate_architecture_map(non_existent)


def test_validate_architecture_map_rejects_missing_views(tmp_path: Path) -> None:
    """Files lacking C4Context or C4Container mermaid diagrams must fail."""
    test_file = tmp_path / "C4.md"
    test_file.write_text("# Title\nNo mermaid here.\n", encoding="utf-8")
    with pytest.raises(ArchitectureMapContractError, match="C4Context"):
        validate_architecture_map(test_file)

    test_file.write_text(
        "```mermaid\nC4Context\nPerson(u, 'User')\n```\n",
        encoding="utf-8",
    )
    with pytest.raises(ArchitectureMapContractError, match="C4Container"):
        validate_architecture_map(test_file)


def test_validate_architecture_map_rejects_empty_tables(tmp_path: Path) -> None:
    """Missing Feature Map or Change Log tables must fail."""
    test_file = tmp_path / "C4.md"
    test_file.write_text(
        "```mermaid\nC4Context\n```\n```mermaid\nC4Container\n```\n",
        encoding="utf-8",
    )
    with pytest.raises(ArchitectureMapContractError, match="Feature Map"):
        validate_architecture_map(test_file)
