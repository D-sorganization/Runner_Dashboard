#!/usr/bin/env python3
"""Import-time smoke check for release artifacts (#3660).

The hub runs ``backend/server.py`` with only ``backend/`` on ``sys.path``; modules
such as ``routers.staff_pr_hygiene`` shim in the artifact root so ``from scripts
import …`` resolves. This script verifies every ``scripts.*`` module imported at
module scope under ``backend/`` is present in a staged or installed artifact tree.

Usage (packager sets ``ARTIFACT_ROOT`` to the throwaway install directory)::

    ARTIFACT_ROOT=/path/to/staged-artifact-root python deploy/artifact_backend_import_smoke.py
"""

from __future__ import annotations

import ast
import importlib
import os
import sys
from pathlib import Path


def _backend_scripts_imports(backend_dir: Path) -> set[str]:
    """Return dotted module names under ``scripts`` imported at module scope."""
    modules: set[str] = set()
    for path in backend_dir.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise SystemExit(f"failed to parse {path}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module == "scripts" or node.module.startswith("scripts."):
                    if node.module == "scripts":
                        for alias in node.names:
                            if alias.name == "*":
                                raise SystemExit(f"wildcard scripts import in {path} is unsupported for smoke")
                            modules.add(f"scripts.{alias.name}")
                    else:
                        modules.add(node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name == "scripts" or name.startswith("scripts."):
                        modules.add(name)
    return modules


def _configure_import_path(artifact_root: Path) -> None:
    backend = artifact_root / "backend"
    scripts = artifact_root / "scripts"
    if not backend.is_dir():
        raise SystemExit(f"artifact missing backend/: {backend}")
    if not scripts.is_dir():
        raise SystemExit(f"artifact missing scripts/: {scripts}")
    for entry in (str(backend), str(artifact_root)):
        if entry not in sys.path:
            sys.path.insert(0, entry)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    files_only = "--files-only" in args

    artifact_root = Path(os.environ.get("ARTIFACT_ROOT", ".")).resolve()
    if not files_only:
        _configure_import_path(artifact_root)
        os.environ.setdefault("AUTODERIVE_FLEET_NODES", "0")
        os.environ.setdefault("FLEET_NODES", "")

    backend_dir = artifact_root / "backend"
    missing: list[str] = []
    for module_name in sorted(_backend_scripts_imports(backend_dir)):
        rel = f"{module_name.replace('.', '/')}.py"
        if not (artifact_root / rel).is_file():
            missing.append(rel)
            continue
        if files_only:
            continue
        importlib.import_module(module_name)

    if missing:
        listed = ", ".join(missing)
        raise SystemExit(f"artifact is missing backend scripts imports: {listed}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
