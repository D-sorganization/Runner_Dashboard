"""Tests for the agent remediation workflow health probe.

Regression guard for D-sorganization/Repository_Management#1483 (fleet-wide
Jules retirement, program D-sorganization/Repository_Management#1505): the
probe used to hardcode a tuple of ``Jules-*.yml`` filenames, so once those
workflows were deleted it reported every one of them as a missing workflow on
the dashboard's Remediation tab. The probe now discovers the live agent
surface from disk, so a future retirement cannot resurrect that failure mode.
"""

from pathlib import Path

from agent_remediation import (
    WorkflowHealthReport,
    inspect_jules_workflows,
    inspect_remediation_workflows,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

RETIRED_JULES_WORKFLOWS = (
    "Jules-Control-Tower.yml",
    "Jules-Auto-Repair.yml",
    "Jules-PR-AutoFix.yml",
    "Jules-Issue-Triage.yml",
    "Jules-Issue-Resolver.yml",
    "Jules-Dispatch.yml",
)


def _write_workflow(workflows_dir: Path, filename: str, body: str) -> None:
    workflows_dir.mkdir(parents=True, exist_ok=True)
    (workflows_dir / filename).write_text(body, encoding="utf-8")


def test_probe_discovers_agent_workflows_from_disk(tmp_path: Path) -> None:
    """Only ``Agent-*`` workflows present on disk are reported."""
    workflows = tmp_path / ".github" / "workflows"
    _write_workflow(
        workflows,
        "Agent-Lease-Reaper.yml",
        'name: Agent Lease Reaper\non:\n  schedule:\n    - cron: "17 * * * *"\n  workflow_dispatch:\n',
    )
    _write_workflow(workflows, "ci-standard.yml", "name: CI Standard\non:\n  pull_request:\n")

    report = inspect_remediation_workflows(tmp_path)

    assert isinstance(report, WorkflowHealthReport)
    assert [entry.workflow_file for entry in report.workflows] == ["Agent-Lease-Reaper.yml"]
    entry = report.workflows[0]
    assert entry.exists is True
    assert entry.workflow_name == "Agent Lease Reaper"
    assert entry.manual_dispatch is True
    assert entry.scheduled is True
    assert entry.trigger_type == "manual"
    assert entry.issues == ()


def test_probe_matches_agent_prefix_case_insensitively(tmp_path: Path) -> None:
    """``agent-panel-review.yml`` is part of the surface despite lowercase naming."""
    workflows = tmp_path / ".github" / "workflows"
    _write_workflow(workflows, "agent-panel-review.yml", "name: Agent Panel Review\non:\n  workflow_dispatch:\n")
    _write_workflow(workflows, "Agent-Fleet-Dashboard.yaml", "name: Agent Fleet Dashboard\non:\n  workflow_dispatch:\n")

    report = inspect_remediation_workflows(tmp_path)

    assert [entry.workflow_file for entry in report.workflows] == [
        "Agent-Fleet-Dashboard.yaml",
        "agent-panel-review.yml",
    ]


def test_probe_never_reports_retired_jules_workflows_against_this_repo() -> None:
    """The live repo must not surface phantom 'Workflow file is missing.' entries."""
    report = inspect_remediation_workflows(REPO_ROOT)

    reported = {entry.workflow_file for entry in report.workflows}
    assert reported.isdisjoint(RETIRED_JULES_WORKFLOWS)
    assert all(entry.exists for entry in report.workflows)
    assert not any("missing" in issue.lower() for entry in report.workflows for issue in entry.issues)


def test_probe_flags_reintroduced_jules_workflow_reference(tmp_path: Path) -> None:
    """A resurrected reference to a retired Jules workflow is reported as an issue."""
    workflows = tmp_path / ".github" / "workflows"
    _write_workflow(
        workflows,
        "Agent-Revived.yml",
        "name: Agent Revived\non:\n  workflow_dispatch:\njobs:\n"
        "  call:\n    uses: ./.github/workflows/Jules-Control-Tower.yml\n",
    )

    report = inspect_remediation_workflows(tmp_path)

    issues = report.workflows[0].issues
    assert any("retired" in issue.lower() for issue in issues), issues


def test_probe_reports_empty_surface_without_inventing_entries(tmp_path: Path) -> None:
    """An empty workflows directory yields no entries and an explanatory summary."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)

    report = inspect_remediation_workflows(tmp_path)

    assert report.workflows == ()
    assert "no agent" in report.summary.lower()


def test_report_keeps_control_tower_summary_key_for_one_release(tmp_path: Path) -> None:
    """Two-step schema change: new ``summary`` ships beside the legacy alias."""
    workflows = tmp_path / ".github" / "workflows"
    _write_workflow(workflows, "Agent-Ok.yml", "name: Agent Ok\non:\n  workflow_dispatch:\n")

    payload = inspect_remediation_workflows(tmp_path).to_dict()

    assert payload["summary"] == payload["control_tower_summary"]
    assert payload["generated_at"]
    assert isinstance(payload["workflows"], list)


def test_legacy_alias_delegates_to_the_new_probe(tmp_path: Path) -> None:
    """``inspect_jules_workflows`` stays importable for one deprecation window."""
    workflows = tmp_path / ".github" / "workflows"
    _write_workflow(workflows, "Agent-Ok.yml", "name: Agent Ok\non:\n  workflow_dispatch:\n")

    legacy = inspect_jules_workflows(tmp_path).to_dict()
    current = inspect_remediation_workflows(tmp_path).to_dict()

    assert legacy["workflows"] == current["workflows"]
    assert legacy["summary"] == current["summary"]


# ---------------------------------------------------------------------------
# The Run button on the same panel must dispatch into the repo the probe read.
# ---------------------------------------------------------------------------


def test_dispatch_endpoint_targets_the_repo_the_probe_inspected() -> None:
    """The panel lists this repo's ``Agent-*.yml``, so Run must dispatch here.

    Before RM#1483 the panel listed fleet ``Jules-*`` workflows that lived in
    Repository_Management, and the dispatch endpoint was hardcoded to that
    repo. With the probe repointed at the local agent surface, dispatching to
    Repository_Management would 404 — or, worse, fire a same-named workflow in
    the wrong repository.
    """
    from routers.remediation import DASHBOARD_REPO, workflow_dispatch_endpoint

    endpoint = workflow_dispatch_endpoint("Agent-Lease-Reaper.yml")

    assert DASHBOARD_REPO not in ("", "Repository_Management")
    assert endpoint.endswith("/actions/workflows/Agent-Lease-Reaper.yml/dispatches")
    assert f"/{DASHBOARD_REPO}/" in endpoint
    assert "Repository_Management" not in endpoint
