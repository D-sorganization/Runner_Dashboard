"""Verify there are no duplicate top-level function declarations.

Two guards:

1. ``legacy/App.tsx`` has no duplicate top-level ``function <Name>``.
2. ``backend/**/*.py`` has no *body-identical* twin of a top-level function
   defined in another module — the copy-paste-drift pattern called out in
   issue #941. Same-named twins drift independently: a fix lands in one copy and
   not the runtime one (the proxy_to_hub credential leak was exactly this). The
   ``server.py`` wiring module is held to a stricter rule: it must contain no
   function body-identical to any other backend module (it is a wiring layer,
   not a home for logic).
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "backend"

# Pre-existing legitimate cross-module body-identical pairs that do NOT involve
# server.py. These are small shared idioms (DB connectors, YAML lock helpers,
# DI shims) that have their own consolidation tickets; allow-listing them keeps
# this guard focused on the server.py god-module sweep (#941) and on *new*
# drift, without forcing an unrelated refactor in the same change.
_ALLOWLISTED_BODY_DUP_NAMES = frozenset(
    {
        "_age_hours",
        "_default_config_dir",
        "_ensure_dict",
        "_locked_yaml_file",
        "_normalize_token",
        "_psutil_dep",
        "_required_string",
        "_run_gh",
        "detect_sharing_violations",
        "get_gpu_info",
        "get_runner_routing_audit",
    }
)


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return a structural hash of a function body, ignoring its docstring."""
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    dumped = ast.dump(ast.Module(body=body, type_ignores=[]), annotate_fields=False)
    return hashlib.sha256(dumped.encode()).hexdigest()[:16]


def _collect_top_level_functions() -> dict[tuple[str, str], set[str]]:
    """Map (name, body-hash) → set of backend modules defining it."""
    index: dict[tuple[str, str], set[str]] = {}
    for path in _BACKEND.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - parse-failure is its own bug
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = (node.name, _function_signature(node))
                index.setdefault(key, set()).add(path.as_posix())
    return index


def test_server_py_has_no_body_identical_twin() -> None:
    """server.py must not define a function body-identical to one elsewhere (#941).

    server.py is the FastAPI wiring module; logic lives in routers/ and helper
    modules. A body-identical twin here is dead, shadowed, or drift-prone code.
    """
    index = _collect_top_level_functions()
    offenders: list[str] = []
    for (name, _hash), modules in index.items():
        server_copies = {m for m in modules if m.endswith("backend/server.py")}
        other_copies = modules - server_copies
        if server_copies and other_copies:
            offenders.append(f"{name}: server.py duplicates {sorted(other_copies)}")
    assert not offenders, "server.py contains body-identical twins of helpers:\n" + "\n".join(sorted(offenders))


def test_no_new_body_identical_backend_duplicates() -> None:
    """No *new* body-identical cross-module backend duplicate may be introduced (#941).

    Existing legitimate idioms are allow-listed; anything else is drift.
    """
    index = _collect_top_level_functions()
    offenders: list[str] = []
    for (name, _hash), modules in index.items():
        if len(modules) > 1 and name not in _ALLOWLISTED_BODY_DUP_NAMES:
            offenders.append(f"{name}: {sorted(modules)}")
    assert not offenders, (
        "New body-identical cross-module duplicates (extract to a shared module, "
        "or add to the allowlist with a tracking issue):\n" + "\n".join(sorted(offenders))
    )


def test_no_duplicate_top_level_functions_in_legacy() -> None:
    """Every top-level ``function <Name>(...)`` must appear exactly once."""
    src = Path(__file__).parent.parent / "frontend" / "src" / "legacy" / "App.tsx"
    assert src.exists(), f"Source file not found: {src}"

    content = src.read_text(encoding="utf-8")

    # Match line-anchored function declarations (not inside block comments)
    # Use MULTILINE so ^ matches start of each line.
    pattern = re.compile(r"^function (\w+)\b", re.MULTILINE)

    names: list[str] = pattern.findall(content)

    # Build a set of duplicates
    seen: set[str] = set()
    duplicates: set[str] = set()
    for n in names:
        if n in seen:
            duplicates.add(n)
        seen.add(n)

    assert not duplicates, f"Duplicate top-level functions in legacy/App.tsx: {sorted(duplicates)}"
