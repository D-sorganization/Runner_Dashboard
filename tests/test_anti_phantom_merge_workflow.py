"""anti-phantom-merge reads PR commits over the REST API and never checks out PR code (RM#1989)."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "anti-phantom-merge.yml"


def _steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["guard"]["steps"]


def test_rule_4_reads_pr_commits_from_the_rest_api() -> None:
    script = "\n".join(s.get("run", "") for s in _steps())
    assert "pulls/$PR/commits" in script
    assert "--paginate" in script
    assert "git log" not in script


def test_no_step_checks_out_code() -> None:
    uses = [s.get("uses", "") for s in _steps()]
    assert not [u for u in uses if u.startswith("actions/checkout")]


def test_no_pr_head_reference_in_the_guard_job() -> None:
    text = yaml.safe_dump(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["guard"]["steps"])
    assert "pull_request.head" not in text
