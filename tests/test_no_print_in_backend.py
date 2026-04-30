"""Sanity test enforcing the "never print()" rule in backend/.

The dashboard CLAUDE.md mandates that backend code use the ``logging``
module via ``log = logging.getLogger("dashboard")`` and never ``print()``.
This test walks ``backend/**/*.py`` and asserts no ``print(...)`` call
appears at module/function level (docstrings and comments are ignored
because we parse the AST).

If a backend file legitimately needs ``print`` (e.g. a debug-only path
inside ``if __name__ == "__main__":``), suppress it with a module-level
``# noqa: T201`` and add the path to ``ALLOWED_PRINT_FILES`` here with a
justification.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

# Files allowed to call ``print``. Add a justification when extending.
ALLOWED_PRINT_FILES: frozenset[str] = frozenset()


def _iter_backend_python_files() -> list[Path]:
    """Return every ``*.py`` file under ``backend/`` except ``.venv``."""
    return [path for path in BACKEND_DIR.rglob("*.py") if ".venv" not in path.parts and "__pycache__" not in path.parts]


def _find_print_calls(tree: ast.AST) -> list[int]:
    """Return line numbers of any ``print(...)`` calls in ``tree``."""
    offending: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "print":
                offending.append(node.lineno)
    return offending


def test_no_print_calls_in_backend() -> None:
    """No backend module may call ``print`` (use the ``log`` logger instead)."""
    violations: list[str] = []
    for path in _iter_backend_python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED_PRINT_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - signals a broken file
            violations.append(f"{rel}: failed to parse ({exc})")
            continue
        for lineno in _find_print_calls(tree):
            violations.append(f"{rel}:{lineno}: print() call is forbidden in backend/")
    assert not violations, "print() calls found in backend/:\n" + "\n".join(violations)
