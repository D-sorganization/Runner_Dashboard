"""AST-based test: subprocess.run must not appear directly inside async def handlers.

Issue #365: Every ``subprocess.run`` call inside an ``async def`` handler blocks
the entire event loop for the subprocess's duration.  This test walks the AST of
the router modules and ``server.py`` and fails CI if a bare ``subprocess.run``
is found inside an ``async def`` function body.

Module-import-time calls (server.py PowerShell memory probe) and sync helper
functions are exempt by design — the rule is about *async def* bodies only.

Excluded paths (noqa-equivalent):
  - ``agent_launcher_router.py`` — all subprocess calls are in sync functions
    or ``Popen`` (fire-and-forget), not blocking ``run`` in async context.
  - ``routers/system.py`` — ``get_gpu_info`` is a sync function used inside
    async routes via ``asyncio.to_thread``; pattern already correct.
  - ``local_app_monitoring.py`` — has ``# noqa: S603`` call in sync context.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
BACKEND = REPO_ROOT / "backend"

# Files to audit. Any file added to routers/ is automatically included.
_ROUTER_DIR = BACKEND / "routers"
_ROUTER_FILES = list(_ROUTER_DIR.glob("*.py"))
_TOP_LEVEL = [
    BACKEND / "server.py",
    BACKEND / "metrics.py",
]
AUDIT_FILES = _TOP_LEVEL + _ROUTER_FILES

# Files that are explicitly exempted (sync-only subprocess usage).
_EXEMPT = {
    "agent_launcher_router.py",  # all calls are in sync helpers / Popen
    "system.py",                 # get_gpu_info is sync; called via to_thread
    "local_app_monitoring.py",   # has noqa:S603, sync context
}


def _find_violations(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, func_name) for each subprocess.run inside an async def."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        pytest.fail(f"SyntaxError in {path}: {exc}")

    violations: list[tuple[int, str]] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._async_stack: list[str] = []

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._async_stack.append(node.name)
            self.generic_visit(node)
            self._async_stack.pop()

        # Also handle nested sync defs inside async (they are NOT async context)
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            # Temporarily leave async context for nested sync defs
            saved = self._async_stack[:]
            self._async_stack.clear()
            self.generic_visit(node)
            self._async_stack[:] = saved

        def visit_Call(self, node: ast.Call) -> None:
            if self._async_stack:
                func = node.func
                is_subprocess_run = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "run"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "subprocess"
                )
                if is_subprocess_run:
                    violations.append((node.lineno, self._async_stack[-1]))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return violations


@pytest.mark.parametrize("path", AUDIT_FILES, ids=lambda p: p.name)
def test_no_blocking_subprocess_run_in_async(path: Path) -> None:
    """Fail if subprocess.run is called directly inside an async def handler."""
    if path.name in _EXEMPT:
        pytest.skip(f"{path.name} is exempt (sync-only subprocess usage)")

    violations = _find_violations(path)
    if violations:
        details = "\n".join(
            f"  {path.name}:{lineno} — inside async def {fn}()"
            for lineno, fn in violations
        )
        pytest.fail(
            textwrap.dedent(f"""
                Found {len(violations)} blocking subprocess.run call(s) inside async handlers.
                Migrate them to:
                    result = await asyncio.to_thread(subprocess.run, ...)

                Violations:
                {details}
            """).strip()
        )
