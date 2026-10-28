#!/usr/bin/env python3
"""Rewrite private fleet hostnames inside string literals only.

The bulk topology scrub must never touch Python (or other) identifiers. A naive
global substring replace turns ``def test_brick_registry`` into invalid syntax
when ``brick`` maps to ``node-c``.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

# Longest keys first so ``controltower-nvme`` wins over ``controltower``.
_LITERAL_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("control-tower-runner-monitoring", "node-a-runner-monitoring"),
    ("control-tower-nvme", "node-a-nvme"),
    ("controltower-nvme", "node-a-nvme"),
    ("control-tower-runner", "node-a-runner"),
    ("controltower-ssd", "node-a-ssd"),
    ("control-tower-nvme", "node-a-nvme"),
    ("deskcomputer.tail2bbcc7.ts.net", "hub-a.fleet-example.ts.net"),
    ("controltower.tail2bbcc7.ts.net", "node-a.fleet-example.ts.net"),
    ("brick.tail2bbcc7.ts.net", "node-c.fleet-example.ts.net"),
    ("oglaptop.tail2bbcc7.ts.net", "node-b.fleet-example.ts.net"),
    ("tail2bbcc7.ts.net", "fleet-example.ts.net"),
    ("DeskComputer", "hub-a"),
    ("ControlTower", "node-a"),
    ("OGLaptop", "node-b"),
    ("Brick", "node-c"),
    ("deskcomputer", "hub-a"),
    ("controltower", "node-a"),
    ("oglaptop", "node-b"),
    ("brick", "node-c"),
    ("dieterolson", "fleet-operator"),
    ("diete", "fleet-operator"),
)

_STRING_CHUNK_RE = re.compile(
    r'("(?:\\.|[^"\\])*")|(\'(?:\\.|[^\'\\])*\')|(`(?:\\.|[^`\\])*`)'
)


def apply_literal_replacements(text: str) -> str:
    out = text
    for old, new in _LITERAL_REPLACEMENTS:
        out = out.replace(old, new)
    return out


class _PythonStringSanitizer(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, str):
            node.value = apply_literal_replacements(node.value)
        return node

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                part.value = apply_literal_replacements(part.value)
        return node


def sanitize_python_source(source: str) -> str:
    """Return source with replacements applied only inside string literals."""
    tree = ast.parse(source)
    _PythonStringSanitizer().visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + ("\n" if source.endswith("\n") else "")


def sanitize_quoted_text(source: str) -> str:
    """Replace inside double/single/backtick quoted regions (non-Python files)."""

    def _sub(match: re.Match[str]) -> str:
        chunk = match.group(0)
        quote = chunk[0]
        body = chunk[1:-1]
        return quote + apply_literal_replacements(body) + quote

    return _STRING_CHUNK_RE.sub(_sub, source)


def sanitize_file_text(path: Path, source: str) -> str:
    if path.suffix == ".py":
        return sanitize_python_source(source)
    return sanitize_quoted_text(source)


def sanitize_path(path: Path, *, dry_run: bool = False) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = sanitize_file_text(path, original)
    if updated == original:
        return False
    if not dry_run:
        path.write_text(updated, encoding="utf-8")
    return True


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Files to rewrite")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    changed = 0
    for path in args.paths:
        if not path.is_file():
            print(f"skip missing {path}", file=sys.stderr)
            continue
        if sanitize_path(path, dry_run=args.dry_run):
            changed += 1
    print(f"updated {changed} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
