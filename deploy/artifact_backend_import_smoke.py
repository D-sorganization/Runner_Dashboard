#!/usr/bin/env python3
"""Import-time smoke check for release artifacts (#3660).

The hub runs ``backend/server.py`` with only ``backend/`` on ``sys.path``; modules
such as ``routers.staff_pr_hygiene`` shim in the artifact root so ``from scripts
import …`` resolves. This script verifies every ``scripts.*`` module imported at
module scope under ``backend/`` is present in a staged or installed artifact tree,
and that the ``code-reviewer`` post-run finalizer never imports repo-root
``shared_scripts`` (#4359).

Some of those scripts put ``clients/fleet`` on ``sys.path`` and import a module
from it (``scripts/cursor_cloud_handoff.py`` imports ``fleet_client``). The
artifact must ship those modules too (#4426): this check fails when one is
missing, and when one resolves from outside the artifact.

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


_CLIENTS_FLEET = ("clients", "fleet")


def _adds_clients_fleet_to_path(tree: ast.AST) -> bool:
    """True when the module names the ``clients/fleet`` directory in a string literal."""
    literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    if any("clients/fleet" in value.replace("\\", "/") for value in literals):
        return True
    return set(_CLIENTS_FLEET) <= literals


def _imported_top_level_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
    return names


def _ships_elsewhere_in_artifact(artifact_root: Path, name: str) -> bool:
    """True when ``name`` is importable from the artifact root or ``backend/``."""
    for base in (artifact_root, artifact_root / "backend"):
        if (base / f"{name}.py").is_file() or (base / name).is_dir():
            return True
    return False


def _installed_outside_clients_fleet(name: str) -> bool:
    """True for a third-party package the running interpreter already has."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    if spec is None or not spec.origin:
        return False
    return Path(spec.origin).resolve().parent.parts[-2:] != _CLIENTS_FLEET


def _client_modules_needed(artifact_root: Path, scripts_modules: set[str]) -> dict[str, str]:
    """Map each ``clients/fleet`` module a backend-imported script needs to that script.

    A script counts only when it names ``clients/fleet`` itself; of its imports,
    the standard library, anything else shipped in the artifact and installed
    third-party packages are left out.
    """
    needed: dict[str, str] = {}
    for module_name in sorted(scripts_modules):
        rel = f"{module_name.replace('.', '/')}.py"
        path = artifact_root / rel
        if not path.is_file():
            continue  # reported by the scripts check
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if not _adds_clients_fleet_to_path(tree):
            continue
        for name in sorted(_imported_top_level_names(tree)):
            if name in sys.stdlib_module_names or name == "__future__":
                continue
            if _ships_elsewhere_in_artifact(artifact_root, name) or _installed_outside_clients_fleet(name):
                continue
            needed.setdefault(name, rel)
    return needed


def _assert_loaded_from_artifact(name: str, loaded_from: Path, artifact_root: Path) -> None:
    expected_dir = artifact_root.joinpath(*_CLIENTS_FLEET).resolve()
    if loaded_from.resolve().parent != expected_dir:
        raise SystemExit(
            f"{name} was imported from {loaded_from}, outside the artifact's clients/fleet ({expected_dir})"
        )


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
    scripts_modules = _backend_scripts_imports(backend_dir)
    client_modules = _client_modules_needed(artifact_root, scripts_modules)
    missing_clients = [
        f"clients/fleet/{name}.py (imported by {script})"
        for name, script in sorted(client_modules.items())
        if not artifact_root.joinpath(*_CLIENTS_FLEET, f"{name}.py").is_file()
    ]
    if missing_clients:
        raise SystemExit("artifact is missing client modules: " + ", ".join(missing_clients))

    for module_name in sorted(scripts_modules):
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

    if not files_only:
        for name in sorted(client_modules):
            module = importlib.import_module(name)
            _assert_loaded_from_artifact(name, Path(str(module.__file__)), artifact_root)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
