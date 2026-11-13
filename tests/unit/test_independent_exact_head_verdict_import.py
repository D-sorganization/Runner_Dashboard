"""independent_exact_head_verdict must import with only ``backend/`` on the path (#4359).

The deploy artifact and ``python backend/server.py`` do not put the repo root
(which holds ``shared_scripts``) on ``sys.path``, so importing it crashed
``finalize_code_reviewer_run`` when it posted the bound verdict (same root cause
as #2994 / #3477). The backend copies are pinned to the ``shared_scripts``
originals here so the two cannot drift.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2] / "backend"

_FULL = "abcdef01" * 5
_SHA_PAIRS = [
    (_FULL, _FULL),
    (_FULL[:7], _FULL),
    (_FULL, _FULL[:12]),
    (f"  {_FULL[:9].upper()} ", _FULL),
    (_FULL[:6], _FULL),
    (_FULL[:7], "f" * 40),
    ("", _FULL),
    (_FULL, "  "),
]


def test_verdict_module_imports_and_builds_body_without_repo_root_on_path() -> None:
    code = (
        f"import sys; sys.path.insert(0, {str(BACKEND)!r}); "
        "import staff.independent_exact_head_verdict as v; "
        "print(v.build_independent_verdict_comment_body("
        "verdict='pass', head_sha='ABC1234', reviewer_model='m')); "
        "assert 'shared_scripts' not in sys.modules"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSTARTUP")}
    proc = subprocess.run(
        [sys.executable, "-E", "-c", code],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PASS @abc1234" in proc.stdout


def test_backend_marker_equals_shared_scripts_original() -> None:
    from staff import independent_exact_head_verdict as backend_copy

    from shared_scripts import independent_exact_head_review as original

    assert backend_copy.INDEPENDENT_REVIEW_MARKER == original.INDEPENDENT_REVIEW_MARKER


@pytest.mark.parametrize(("recorded", "head"), _SHA_PAIRS)
def test_backend_head_sha_matches_agrees_with_shared_scripts_original(recorded: str, head: str) -> None:
    from staff import independent_exact_head_verdict as backend_copy

    from shared_scripts import independent_exact_head_review as original

    assert backend_copy.head_sha_matches(recorded, head) is original.head_sha_matches(recorded, head)


def test_backend_comment_body_is_parsed_by_shared_scripts_reader() -> None:
    from staff.independent_exact_head_verdict import build_independent_verdict_comment_body

    from shared_scripts.independent_exact_head_review import parse_independent_review_comment

    body = build_independent_verdict_comment_body(verdict="PASS", head_sha=_FULL, reviewer_model="model-x")
    parsed = parse_independent_review_comment(body)

    assert parsed is not None
    assert (parsed.verdict, parsed.head_sha, parsed.reviewer_model) == ("PASS", _FULL, "model-x")
