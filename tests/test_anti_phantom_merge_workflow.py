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


def test_guard_scripts_checkout_is_sparse_on_base_ref_only() -> None:
    checkout = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout")]
    assert len(checkout) == 1
    with_block = checkout[0].get("with") or {}
    assert "phantom_guard_issue_paths.py" in str(with_block.get("sparse-checkout", ""))
    assert "phantom_guard_title.py" in str(with_block.get("sparse-checkout", ""))
    assert "${{ github.event.pull_request.base.ref" in str(with_block.get("ref", ""))


def test_no_pr_head_reference_in_the_guard_job() -> None:
    text = yaml.safe_dump(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["guard"]["steps"])
    assert "pull_request.head" not in text
