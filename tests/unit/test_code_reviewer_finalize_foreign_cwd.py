"""Code-reviewer finalizer must not import repo-root ``shared_scripts`` (#4359).

Hosted ``code-reviewer`` runs launch provider subprocesses with cwd in arbitrary
fleet repo worktrees. Post-run classification imports
``staff.review`` from the hub backend process (``backend/`` only on ``sys.path``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from deploy.staff_code_reviewer_finalize_probe import (
    assert_code_reviewer_finalizer_has_no_shared_scripts_imports,
    module_level_shared_scripts_imports,
    verify_code_reviewer_finalize_from_foreign_cwd,
)

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def test_finalizer_modules_have_no_module_level_shared_scripts_imports() -> None:
    assert_code_reviewer_finalizer_has_no_shared_scripts_imports(BACKEND)


def test_static_guard_fails_on_pre_fix_shared_scripts_import(tmp_path: Path) -> None:
    """Regression guard must catch the #4396 bug shape (module-level shared_scripts import)."""
    broken = tmp_path / "staff" / "independent_exact_head_verdict.py"
    broken.parent.mkdir(parents=True)
    broken.write_text(
        "from shared_scripts.independent_exact_head_review import head_sha_matches\n",
        encoding="utf-8",
    )
    violations = module_level_shared_scripts_imports(broken)
    assert violations


def test_finalize_code_reviewer_run_from_foreign_cwd_without_shared_scripts(tmp_path: Path) -> None:
    foreign = tmp_path / "empty-fleet-checkout"
    rc, out, err = verify_code_reviewer_finalize_from_foreign_cwd(
        backend_dir=BACKEND,
        foreign_cwd=foreign,
        python_executable=sys.executable,
    )
    assert rc == 0, f"stdout={out!r} stderr={err!r}"
    assert out.strip() == "ok"
