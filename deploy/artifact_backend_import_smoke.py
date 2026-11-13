#!/usr/bin/env python3
"""Import-time smoke check for release artifacts (#3660).

The hub runs ``backend/server.py`` with only ``backend/`` on ``sys.path``; modules
such as ``routers.staff_pr_hygiene`` shim in the artifact root so ``from scripts
import …`` resolves. This script verifies every ``scripts.*`` module imported at
module scope under ``backend/`` is present in a staged or installed artifact tree,
and that the ``code-reviewer`` post-run finalizer never imports repo-root
``shared_scripts`` (#4359).

Usage (packager sets ``ARTIFACT_ROOT`` to the throwaway install directory)::

    ARTIFACT_ROOT=/path/to/staged-artifact-root python deploy/artifact_backend_import_smoke.py
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

_DEPLOY_DIR = Path(__file__).resolve().parent


def _load_finalize_probe() -> ModuleType:
    path = _DEPLOY_DIR / "staff_code_reviewer_finalize_probe.py"
    spec = importlib.util.spec_from_file_location("_staff_code_reviewer_finalize_probe", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"failed to load staff finalize probe from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_finalize_probe = _load_finalize_probe()
assert_code_reviewer_finalizer_has_no_shared_scripts_imports = (
    _finalize_probe.assert_code_reviewer_finalizer_has_no_shared_scripts_imports
)
verify_code_reviewer_finalize_from_foreign_cwd = _finalize_probe.verify_code_reviewer_finalize_from_foreign_cwd


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
    assert_code_reviewer_finalizer_has_no_shared_scripts_imports(backend_dir)
    if not files_only:
        foreign_cwd = artifact_root / ".smoke-foreign-workdir"
        rc, out, err = verify_code_reviewer_finalize_from_foreign_cwd(
            backend_dir=backend_dir,
            foreign_cwd=foreign_cwd,
            python_executable=sys.executable,
        )
        if rc != 0:
            detail = (err or out or "").strip()[:500]
            raise SystemExit(f"code-reviewer finalize smoke failed (rc={rc}): {detail}")

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
