"""Regression tests for scripts/sanitize_private_topology.py."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.sanitize_private_topology import (
    apply_literal_replacements,
    sanitize_file_text,
    sanitize_python_source,
)


def test_apply_literal_replacements_maps_example_host() -> None:
    assert apply_literal_replacements("http://deskcomputer.tail2bbcc7.ts.net:8321") == (
        "http://hub-a.fleet-example.ts.net:8321"
    )


def test_sanitize_python_leaves_identifiers_and_rewrites_string_literals() -> None:
    source = (
        "def test_checked_in_brick_registry() -> None:\n"
        '    brick = next(m for m in machines if m["name"] == "Brick")\n'
        '    assert brick["runners"] == {"min": 0}\n'
    )
    out = sanitize_python_source(source)
    tree = ast.parse(out)
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert names == {"test_checked_in_brick_registry"}
    assert "hub-a" not in out
    assert "node-c" in out
    assert "brick = next" in out


def test_sanitize_python_does_not_rename_def_when_alias_contains_hyphen() -> None:
    source = (
        "def test_local_cursor_on_brick_registers() -> None:\n"
        '    host = "brick.tail2bbcc7.ts.net"\n'
    )
    out = sanitize_python_source(source)
    assert "def test_local_cursor_on_brick_registers" in out
    assert "node-c.fleet-example.ts.net" in out


def test_sanitize_file_text_python_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text('x = "deskcomputer"\n', encoding="utf-8")
    assert "hub-a" in sanitize_file_text(path, path.read_text(encoding="utf-8"))
