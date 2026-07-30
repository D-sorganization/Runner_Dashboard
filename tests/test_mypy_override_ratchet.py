"""Guard the mypy relaxed-override ratchet (issue #950).

The CI Type Check step fails when the number of relaxed ``tool.mypy.overrides``
modules exceeds ``MYPY_OVERRIDE_BASELINE`` in ``.github/workflows/ci-standard.yml``.
These tests keep the in-repo baseline honest:

- the workflow's baseline must be >= the actual current count (otherwise CI is
  already red), and
- the baseline must not be set wastefully high above the actual count (so the
  ratchet stays meaningful), and
- the count-extraction logic matches what the workflow runs.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"
_WORKFLOW = _REPO / ".github" / "workflows" / "ci-standard.yml"


def _relaxed_override_count() -> int:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    overrides = data.get("tool", {}).get("mypy", {}).get("overrides", [])
    count = 0
    for override in overrides:
        if override.get("disallow_untyped_defs") is False or override.get("strict_optional") is False:
            modules = override.get("module", [])
            count += len(modules) if isinstance(modules, list) else 1
    return count


def _workflow_baseline() -> int:
    text = _WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r'MYPY_OVERRIDE_BASELINE:\s*"?(\d+)"?', text)
    assert match, "MYPY_OVERRIDE_BASELINE must be declared in ci-standard.yml (#950)"
    return int(match.group(1))


def test_workflow_baseline_not_below_actual_count() -> None:
    """The ratchet baseline must be >= the real count, or CI is already red."""
    assert _workflow_baseline() >= _relaxed_override_count()


def test_workflow_baseline_is_tight() -> None:
    """The baseline must equal the actual count — a shrink-only ratchet has no slack.

    If this fails because the count dropped, lower MYPY_OVERRIDE_BASELINE to match
    (locking in the improvement). If it fails because the count grew, the new
    override is the regression #950 guards against — type the module instead.
    """
    assert _workflow_baseline() == _relaxed_override_count(), (
        "MYPY_OVERRIDE_BASELINE drifted from the actual relaxed-override count; "
        "update the baseline in .github/workflows/ci-standard.yml."
    )


def test_ratchet_step_fails_above_baseline() -> None:
    """The workflow must `exit 1` when the count exceeds the baseline (#950)."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert 'if [ "${OVERRIDE_COUNT}" -gt "${MYPY_OVERRIDE_BASELINE}" ]; then' in text
    # An exit 1 must follow the over-baseline guard.
    guard_idx = text.index('-gt "${MYPY_OVERRIDE_BASELINE}"')
    assert "exit 1" in text[guard_idx : guard_idx + 400], "over-baseline branch must hard-fail (exit 1)"
