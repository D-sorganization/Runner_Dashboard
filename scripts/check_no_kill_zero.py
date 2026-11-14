#!/usr/bin/env python3
"""Fail on ``os.kill(x, 0)`` and ``os.kill(x, signal.CTRL_C_EVENT)`` (#4421).

On Windows both send CTRL+C to every process attached to the console. Use
``scripts/pid_probe.py::pid_alive`` to ask whether a process exists.

Usage::

    python scripts/check_no_kill_zero.py            # scan the repository
    python scripts/check_no_kill_zero.py FILE ...   # scan the given files

Exit 0 when clean, 1 when a violation is found. A file that cannot be parsed
is reported as a warning and does not fail the check: it cannot run either.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# The only file allowed to call os.kill(pid, 0): its POSIX branch.
ALLOWED = frozenset({"scripts/pid_probe.py"})
SCAN_DIRS = ("backend", "clients", "deploy", "scripts", "tests")
_SKIP_PARTS = frozenset({"__pycache__", ".venv", "venv", "node_modules", "wheels"})


def _is_kill(func: ast.expr, kill_names: set[str], os_names: set[str]) -> bool:
    if isinstance(func, ast.Attribute) and func.attr == "kill":
        return isinstance(func.value, ast.Name) and func.value.id in os_names
    return isinstance(func, ast.Name) and func.id in kill_names


def _signal_problem(arg: ast.expr) -> str | None:
    if isinstance(arg, ast.Constant) and arg.value == 0 and not isinstance(arg.value, bool):
        return "signal 0"
    name = arg.attr if isinstance(arg, ast.Attribute) else arg.id if isinstance(arg, ast.Name) else ""
    return "CTRL_C_EVENT" if name == "CTRL_C_EVENT" else None


def _imported_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Return (names bound to the os module, names bound to os.kill)."""
    os_names: set[str] = {"os"}
    kill_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            os_names.update(alias.asname for alias in node.names if alias.name == "os" and alias.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            kill_names.update(alias.asname or alias.name for alias in node.names if alias.name == "kill")
    return os_names, kill_names


def find_violations(source: str, filename: str) -> list[str]:
    """Return one ``file:line: message`` per unsafe ``os.kill`` call in ``source``."""
    tree = ast.parse(source, filename=filename)
    os_names, kill_names = _imported_names(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_kill(node.func, kill_names, os_names):
            continue
        signal_arg = node.args[1] if len(node.args) > 1 else None
        for keyword in node.keywords:
            if keyword.arg in {"signal", "sig"}:
                signal_arg = keyword.value
        problem = _signal_problem(signal_arg) if signal_arg is not None else None
        if problem:
            found.append(
                f"{filename}:{node.lineno}: os.kill with {problem} sends CTRL+C to the whole console on Windows; "
                "use scripts/pid_probe.py::pid_alive"
            )
    return found


def _repo_files(root: Path) -> Iterable[Path]:
    for directory in SCAN_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if not _SKIP_PARTS & set(path.relative_to(root).parts):
                yield path


def check(paths: Iterable[Path], root: Path = REPO_ROOT) -> tuple[list[str], list[str]]:
    """Return (violations, unparsable files) for ``paths``; allow-listed files are skipped."""
    violations: list[str] = []
    unparsable: list[str] = []
    for path in paths:
        resolved = path.resolve()
        try:
            rel = resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = path.as_posix()
        if rel in ALLOWED or resolved.suffix != ".py":
            continue
        try:
            violations.extend(find_violations(resolved.read_text(encoding="utf-8"), rel))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            unparsable.append(f"{rel}: {exc}")
    return violations, unparsable


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    paths = [Path(arg) for arg in args] if args else list(_repo_files(REPO_ROOT))
    violations, unparsable = check(paths)
    for line in violations:
        sys.stderr.write(line + "\n")
    for line in unparsable:
        sys.stderr.write(f"warning: not checked, cannot parse {line}\n")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
