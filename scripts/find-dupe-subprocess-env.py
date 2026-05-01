"""Fail if subprocess environment scrubbing is reimplemented outside security.py."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "backend" / "security.py"
SEARCH_ROOTS = (ROOT / "backend",)
FUNCTION_NAMES = {"safe_subprocess_env", "_safe_subprocess_env"}


def main() -> int:
    duplicates: list[str] = []
    for search_root in SEARCH_ROOTS:
        for path in search_root.rglob("*.py"):
            if path == CANONICAL:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in FUNCTION_NAMES:
                    duplicates.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    if duplicates:
        print("Duplicate subprocess environment scrubbers found; import security.safe_subprocess_env instead:")
        for duplicate in duplicates:
            print(f"  {duplicate}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
