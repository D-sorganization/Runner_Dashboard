"""Contract tests for the reusable PR freshness guard workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def test_reusable_pr_guard_checks_out_before_local_action() -> None:
    data = yaml.safe_load(
        (_WORKFLOWS / "reusable-pr-freshness-guard.yml").read_text(encoding="utf-8")
    )
    steps = data["jobs"]["guard"]["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@")
    assert steps[1]["uses"] == "./.github/actions/pr-early-exit"
