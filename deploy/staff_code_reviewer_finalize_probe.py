"""Probe that code-reviewer finalization never needs repo-root ``shared_scripts`` (#4359).

The hub runs ``python backend/server.py`` with only ``backend/`` importable. Staff
runs execute provider CLIs from arbitrary fleet repo worktrees (Tools, RM, …), so
the post-run finalizer must import from ``staff.*`` alone — not
``shared_scripts.independent_exact_head_review``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

_FINALIZER_REL_PATHS: tuple[str, ...] = (
    "staff/independent_exact_head_verdict.py",
    "staff/review.py",
    "staff/staff_github_pr_review.py",
)

_PROBE = r"""
import os
import sys
from pathlib import Path

backend = Path(os.environ["ARTIFACT_BACKEND"]).resolve()
foreign = Path(os.environ["ARTIFACT_FOREIGN_CWD"]).resolve()
foreign.mkdir(parents=True, exist_ok=True)
os.chdir(foreign)

# Mirror production: backend/ is importable; repo root (shared_scripts/) must not be.
repo_root = backend.parent
if str(backend) not in sys.path:
    sys.path.insert(0, str(backend))
sys.path[:] = [
    p
    for p in sys.path
    if p == str(backend) or (p and Path(p).resolve() != repo_root.resolve() and not p.endswith("shared_scripts"))
]
for key in list(sys.modules):
    if key == "shared_scripts" or key.startswith("shared_scripts."):
        del sys.modules[key]

from unittest.mock import patch

from staff.review import finalize_code_reviewer_run

_HEAD = "c" * 40
with patch(
    "staff.independent_exact_head_verdict.fetch_pull_head_sha",
    return_value=_HEAD,
):
    with patch("staff.staff_github_pr_review.ensure_runner_dashboard_formal_approve"):
        out = finalize_code_reviewer_run(
            status="succeeded",
            failure_class="",
            retryable=False,
            error="",
            remediation="",
            result_line="STAFF_RESULT: review approve #1",
            same_provider=False,
            repo="Tools",
            reviewer_model="codex",
            expected_pr_number=1,
        )
if out.status != "succeeded":
    raise SystemExit(f"unexpected outcome status={out.status!r}")
if "shared_scripts" in sys.modules:
  raise SystemExit("shared_scripts was imported during code-reviewer finalize")
print("ok")
"""


def module_level_shared_scripts_imports(path: Path) -> list[str]:
    """Return human-readable violations for top-level ``shared_scripts`` imports."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]
    violations: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "shared_scripts" or node.module.startswith("shared_scripts."):
                violations.append(f"{path}: imports {node.module} at module scope")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "shared_scripts" or alias.name.startswith("shared_scripts."):
                    violations.append(f"{path}: imports {alias.name} at module scope")
    return violations


def assert_code_reviewer_finalizer_has_no_shared_scripts_imports(backend_dir: Path) -> None:
    """Static guard: finalizer modules must not import ``shared_scripts`` at load time."""
    violations: list[str] = []
    for rel in _FINALIZER_REL_PATHS:
        path = backend_dir / rel
        if not path.is_file():
            violations.append(f"missing finalizer module: {path}")
            continue
        violations.extend(module_level_shared_scripts_imports(path))
    if violations:
        listed = "; ".join(violations)
        raise SystemExit(f"code-reviewer finalizer must not import shared_scripts: {listed}")


def verify_code_reviewer_finalize_from_foreign_cwd(
    *,
    backend_dir: Path,
    foreign_cwd: Path,
    python_executable: str | None = None,
) -> tuple[int, str, str]:
    """Run finalize in a subprocess with cwd outside any fleet checkout."""
    foreign_cwd.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSTARTUP")}
    env["PYTHONPATH"] = ""
    env["AUTODERIVE_FLEET_NODES"] = "0"
    env["FLEET_NODES"] = ""
    env["ARTIFACT_BACKEND"] = str(backend_dir.resolve())
    env["ARTIFACT_FOREIGN_CWD"] = str(foreign_cwd.resolve())
    proc = subprocess.run(
        [python_executable or sys.executable, "-E", "-c", _PROBE],
        cwd=str(foreign_cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr
