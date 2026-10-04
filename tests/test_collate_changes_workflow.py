"""Shape of the post-merge change-fragment collation (RM-5, Repository_Management#1894).

Each pull request adds ``changes/<issue>-<slug>.md`` instead of editing the
shared docs files. ``collate-changes.yml`` folds merged fragments into
``SPEC.md`` and the development log. It never pushes to ``main`` (the branch
ruleset rejects that): it proposes the result as a pull request armed by
``scripts/automerge_guard.py``. Ported from Repository_Management
``tests/test_collate_changes_workflow.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = _ROOT / ".github" / "workflows" / "collate-changes.yml"
_SHA_PIN = re.compile(r"@[0-9a-f]{40}(\s|$)")


def _workflow() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(WORKFLOW.read_text("utf-8")))


def _triggers() -> dict[str, Any]:
    data = _workflow()
    return cast("dict[str, Any]", data.get("on") or data.get(True))


def _job() -> dict[str, Any]:
    jobs = _workflow()["jobs"]
    assert len(jobs) == 1
    return cast("dict[str, Any]", next(iter(jobs.values())))


def _scripts() -> str:
    return "\n".join(str(step.get("run", "")) for step in _job()["steps"])


def test_runs_only_on_push_to_main_touching_fragments() -> None:
    triggers = _triggers()
    assert set(triggers) <= {"push", "workflow_dispatch"}
    assert "merge_group" not in triggers, "a no-op in the merge queue"
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["push"]["paths"] == ["changes/**"]


def test_routes_to_the_self_hosted_fleet_with_a_timeout() -> None:
    assert _job()["runs-on"] == "d-sorg-fleet"
    assert _job()["timeout-minutes"] <= 15


def test_has_the_required_concurrency_protection() -> None:
    concurrency = _workflow()["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    assert "collate-changes" in concurrency["group"]


def test_collates_every_fragment_keyed_by_its_merged_pr() -> None:
    script = _scripts()
    assert "scripts/changes_fragment.py collate" in script
    assert "--pr" in script and "--sha" in script
    # The PR is the one that added the fragment, read from the commit.
    assert "--diff-filter=A" in script
    assert "/commits/" in script and "/pulls" in script


def test_never_pushes_to_main_and_proposes_through_the_guard() -> None:
    script = _scripts()
    assert "HEAD:main" not in script
    assert "push origin main" not in script
    assert "scripts/automerge_guard.py" in script
    assert "--arm" in script
    assert "gh pr merge" not in script


def test_a_held_collation_pr_is_never_force_pushed() -> None:
    script = _scripts()
    held = script.index('automerge_guard.py "$REPO_SLUG" "$PR_NUM"; then')
    assert held < script.index("git push")


def test_needs_full_history_to_find_the_adding_commit() -> None:
    checkout = next(step for step in _job()["steps"] if str(step.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0


def test_actions_are_pinned_to_full_commit_shas() -> None:
    for step in _job()["steps"]:
        uses = str(step.get("uses", ""))
        if uses:
            assert _SHA_PIN.search(uses + "\n"), f"unpinned action: {uses}"


def test_permissions_are_minimal() -> None:
    assert _workflow()["permissions"] == {"contents": "write", "pull-requests": "write"}


def test_collation_pr_body_acknowledges_fragment_deletions() -> None:
    """Every collation deletes fragments; the guard holds unacknowledged deletions.

    Extract the body the workflow sends and run it through the guard's own
    check, so the two can never drift apart.
    """
    # The guard is vendored by the change-fragments PR; this PR is workflow-only.
    automerge_guard = pytest.importorskip("scripts.automerge_guard", reason="merge the change-fragments PR first")

    match = re.search(r'PR_BODY="\$\(printf \'(.*?)\'\)"', _scripts(), re.DOTALL)
    assert match, "collate-changes.yml must build PR_BODY with printf"
    body = match.group(1).replace("\\n", "\n")
    assert automerge_guard._deletions_acknowledged(set(), body)


@pytest.mark.parametrize("name", ["collate-changes.yml"])
def test_is_classified_in_the_runner_routing_policy(name: str) -> None:
    import json

    policy = json.loads((_ROOT / "config" / "workflow_runner_routing_policy.json").read_text("utf-8"))
    assert any(name in cls["workflows"] for cls in policy["workflow_classes"].values())
