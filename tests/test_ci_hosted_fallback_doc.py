"""#4268 H-06: the hosted-fallback runbook must describe the workflow as it is.

The runbook tells an operator which switches move PR and merge-queue CI off the
self-hosted fleet, and which jobs stay on the fleet whatever the switches say.
These tests read ``ci-standard.yml`` and fail when the runbook and the workflow
disagree, so the document cannot go stale without a red check.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI_STANDARD = WORKFLOWS / "ci-standard.yml"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "ci-hosted-fallback.md"
FLEET_ONLY_HEADING = "## Jobs That Stay on the Fleet"


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path
    return data


def _is_fixed_fleet(runs_on: Any) -> bool:
    """True for a literal self-hosted ``runs-on`` that no switch can change."""
    labels = runs_on if isinstance(runs_on, list) else [runs_on]
    return any(isinstance(label, str) and label == "self-hosted" for label in labels)


def _fleet_only_jobs() -> set[str]:
    """Jobs of ``ci-standard.yml`` pinned to the fleet, directly or via a local reusable workflow."""
    pinned: set[str] = set()
    for job_id, job in _load(CI_STANDARD)["jobs"].items():
        uses = job.get("uses", "")
        if uses.startswith("./.github/workflows/"):
            called = _load(REPO_ROOT / uses[2:])
            if any(_is_fixed_fleet(inner.get("runs-on")) for inner in called["jobs"].values()):
                pinned.add(job_id)
        elif _is_fixed_fleet(job.get("runs-on")):
            pinned.add(job_id)
    return pinned


def _switch_variables() -> set[str]:
    """Repository variables read by the ``pick-runner`` selection step."""
    steps = _load(CI_STANDARD)["jobs"]["pick-runner"]["steps"]
    (select,) = [step for step in steps if step.get("id") == "select"]
    return set(re.findall(r"vars\.([A-Z_]+)", " ".join(str(v) for v in select["env"].values())))


def _runbook_fleet_only_jobs() -> set[str]:
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text.split(FLEET_ONLY_HEADING, 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^- `([a-z0-9-]+)`", section, flags=re.MULTILINE))


def test_runbook_names_every_runner_switch() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    switches = _switch_variables()
    assert switches, "pick-runner reads no repository variables; the runbook premise changed"
    missing = sorted(name for name in switches if f"`{name}" not in text)
    assert not missing, f"runbook does not mention runner switches: {missing}"
    assert "`priority:expedite`" in text


def test_runbook_lists_exactly_the_jobs_pinned_to_the_fleet() -> None:
    assert _runbook_fleet_only_jobs() == _fleet_only_jobs()


def test_runner_labels_doc_links_the_runbook() -> None:
    labels_doc = (REPO_ROOT / "docs" / "runner-labels.md").read_text(encoding="utf-8")
    assert "runbooks/ci-hosted-fallback.md" in labels_doc
