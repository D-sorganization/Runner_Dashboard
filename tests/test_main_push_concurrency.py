"""Main-push CI runs must never be cancelled by a newer merge.

Node auto-deploy and main-ci-auto-release only act on a main commit whose blocking
checks are all green. A ``cancel-in-progress`` group shared by successive main pushes
cancels the previous commit's checks when the next merge lands, so no commit ever
becomes green+released. Only superseded ``pull_request`` runs may be cancelled.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_PR_ONLY_CANCEL = "${{ github.event_name == 'pull_request' }}"

# Workflows that produce deploy-blocking check-runs on main pushes (#2491 contract).
_MAIN_PUSH_BLOCKING = [
    "frontend-tests.yml",
    "docker-build.yml",
    "ci-secrets.yml",
    "architecture-map-contract.yml",
    "ci-auto-deploy-regression.yml",
]

# Post-merge CI Standard is not deploy-blocking; superseded main-push runs may cancel.
_CI_STANDARD_MAIN_PUSH_CANCEL = "ci-standard.yml"


def _load(name: str) -> dict:
    data = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize("name", _MAIN_PUSH_BLOCKING)
def test_main_push_runs_are_not_cancel_in_progress(name: str) -> None:
    data = _load(name)
    triggers = data.get(True) or data.get("on")
    assert "push" in triggers, f"{name}: expected a push trigger"
    block = data["concurrency"]
    assert block["cancel-in-progress"] == _PR_ONLY_CANCEL, (
        f"{name}: cancel-in-progress must be limited to pull_request so main pushes are never cancelled"
    )


def test_ci_standard_main_push_cancels_superseded_runs() -> None:
    data = _load(_CI_STANDARD_MAIN_PUSH_CANCEL)
    block = data["concurrency"]
    assert block["cancel-in-progress"] != _PR_ONLY_CANCEL
    assert "push" in str(block["cancel-in-progress"])
    assert "refs/heads/main" in str(block["cancel-in-progress"])
    group = block["group"]
    assert "refs/heads/main' && 'main'" in group


@pytest.mark.parametrize("name", _MAIN_PUSH_BLOCKING)
def test_main_push_groups_are_unique_per_commit(name: str) -> None:
    group = _load(name)["concurrency"]["group"]
    # PRs share a group per ref; merge_group is keyed per queue PR; push is per sha.
    assert "github.event_name" in group
    assert "github.sha" in group
    assert "merge_group-pr-" in group or "github.ref" in group


# Workflows that already ran in the merge queue; the queue needs them to keep reporting.
_MERGE_QUEUE = [
    *_MAIN_PUSH_BLOCKING,
    _CI_STANDARD_MAIN_PUSH_CANCEL,
]
_MERGE_QUEUE = [n for n in _MERGE_QUEUE if n not in {"architecture-map-contract.yml", "ci-secrets.yml"}]


@pytest.mark.parametrize("name", _MERGE_QUEUE)
def test_merge_group_trigger_preserved(name: str) -> None:
    triggers = _load(name).get(True) or _load(name).get("on")
    assert "merge_group" in triggers, f"{name}: merge queue needs merge_group"
