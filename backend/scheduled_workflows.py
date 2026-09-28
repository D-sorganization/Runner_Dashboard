"""Read-only scheduled workflow inventory helpers.

This module scans organization repositories for GitHub Actions workflows,
extracts cron expressions from workflow YAML, and enriches each workflow with
its latest run where GitHub exposes one. It is deliberately side-effect free:
callers inject GitHub API helpers, which keeps the module easy to test with
mocked responses.

The module also exposes a dry-run plan model for future workflow changes.
That plan is descriptive only; no write actions are performed here.
"""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from time_utils import utc_now_iso

GhJson = Callable[[str], Awaitable[Any]]

# Bounded concurrency for the per-repository walk (issue #1745): a serial walk
# over 41 repos at 28-68 workflows each took over 1,200 requests and never
# finished inside the hub's answer budget. Six repos in flight keeps the walk
# fast without hammering the GitHub API rate limit.
_REPO_CONCURRENCY = 6

# Workflow-file cron expressions keyed by git blob sha: a file's content never
# changes under a given sha, so each file is parsed once per edit instead of
# once per refresh (mirrors ``_WORKFLOW_TRIGGER_CACHE`` in
# ``routers/runs_workflows.py``).
_CRON_BY_BLOB_SHA: dict[str, tuple[str, ...]] = {}

_ON_KEY_RE = re.compile(r"^(?P<indent>[ \t]*)(?:'on'|\"on\"|on)\s*:\s*(?:#.*)?$")
_SCHEDULE_KEY_RE = re.compile(r"^(?P<indent>[ \t]*)schedule\s*:\s*(?:#.*)?$")
_CRON_LINE_RE = re.compile(r"^(?P<indent>[ \t]*)(?:-\s*)?cron:\s*(?P<value>[^\r\n#]+?)(?:\s+#.*)?$")


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def extract_cron_expressions(workflow_yaml: str) -> list[str]:
    """Return ordered cron expressions found in a workflow YAML document."""
    matches: list[str] = []
    in_on_block = False
    on_indent = -1
    on_child_indent: int | None = None
    in_schedule_block = False
    schedule_indent = -1

    for raw_line in workflow_yaml.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" \t"))

        if in_schedule_block and (
            indent < schedule_indent or (indent == schedule_indent and not stripped.startswith("-"))
        ):
            in_schedule_block = False

        if in_schedule_block:
            cron_match = _CRON_LINE_RE.match(raw_line)
            if cron_match:
                value = cron_match.group("value").strip().strip("'\"")
                if value and value not in {"|", ">", "|-", ">-"}:
                    matches.append(value)
            continue

        if in_on_block and indent <= on_indent:
            in_on_block = False
            on_child_indent = None

        if in_on_block:
            if on_child_indent is None and indent > on_indent:
                on_child_indent = indent
            if indent == on_child_indent and _SCHEDULE_KEY_RE.match(raw_line):
                in_schedule_block = True
                schedule_indent = indent
            continue

        on_match = _ON_KEY_RE.match(raw_line)
        if on_match:
            in_on_block = True
            on_indent = len(on_match.group("indent"))
            on_child_indent = None

    return _unique(matches)


@dataclass(frozen=True, slots=True)
class ScheduledWorkflowRunSnapshot:
    """Best-effort summary of the latest run for a workflow."""

    run_id: int | None
    status: str
    conclusion: str | None
    html_url: str | None
    created_at: str | None
    updated_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScheduledWorkflowEntry:
    """A workflow entry from a repository inventory scan."""

    workflow_id: int | None
    workflow_name: str
    workflow_path: str
    state: str
    enabled: bool
    scheduled: bool
    schedule_source: str
    cron_expressions: tuple[str, ...] = field(default_factory=tuple)
    latest_run: ScheduledWorkflowRunSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.latest_run is not None:
            data["latest_run"] = self.latest_run.to_dict()
        data["cron_expressions"] = list(self.cron_expressions)
        return data


@dataclass(frozen=True, slots=True)
class ScheduledWorkflowRepositoryInventory:
    """Inventory payload for one repository."""

    repository: str
    archived: bool
    default_branch: str | None
    workflow_count: int
    scheduled_workflow_count: int
    workflows: tuple[ScheduledWorkflowEntry, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["workflows"] = [workflow.to_dict() for workflow in self.workflows]
        return data


@dataclass(frozen=True, slots=True)
class ScheduledWorkflowDryRunStep:
    """A proposed future change that is tracked in dry-run form only."""

    action: str
    repository: str
    workflow_path: str
    workflow_name: str
    reason: str
    requires_confirmation: bool = True
    audit_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScheduledWorkflowDryRunPlan:
    """Descriptive change plan for future workflow operations."""

    mode: str = "dry_run"
    write_actions_allowed: bool = False
    confirmation_required: bool = True
    audit_required: bool = True
    steps: tuple[ScheduledWorkflowDryRunStep, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["steps"] = [step.to_dict() for step in self.steps]
        return data


@dataclass(frozen=True, slots=True)
class ScheduledWorkflowInventoryReport:
    """Org-wide inventory of repository workflow schedules."""

    organization: str
    generated_at: str
    repository_count: int
    scheduled_workflow_count: int
    repositories: tuple[ScheduledWorkflowRepositoryInventory, ...]
    dry_run_plan: ScheduledWorkflowDryRunPlan

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["repositories"] = [repo.to_dict() for repo in self.repositories]
        data["dry_run_plan"] = self.dry_run_plan.to_dict()
        return data


def _build_run_snapshot(run: dict[str, Any]) -> ScheduledWorkflowRunSnapshot:
    return ScheduledWorkflowRunSnapshot(
        run_id=int(run["id"]) if run.get("id") is not None else None,
        status=str(run.get("status") or ""),
        conclusion=(str(run["conclusion"]) if run.get("conclusion") is not None else None),
        html_url=str(run.get("html_url") or "") or None,
        created_at=str(run.get("created_at") or "") or None,
        updated_at=str(run.get("updated_at") or "") or None,
    )


def _build_dry_run_plan(
    repositories: list[ScheduledWorkflowRepositoryInventory],
) -> ScheduledWorkflowDryRunPlan:
    steps: list[ScheduledWorkflowDryRunStep] = []
    for repository in repositories:
        for workflow in repository.workflows:
            if workflow.scheduled and workflow.enabled and workflow.latest_run is None:
                steps.append(
                    ScheduledWorkflowDryRunStep(
                        action="seed-observability",
                        repository=repository.repository,
                        workflow_path=workflow.workflow_path,
                        workflow_name=workflow.workflow_name,
                        reason=(
                            "Scheduled workflow has no retrievable latest run yet; "
                            "future schedule changes should be reviewed with an "
                            "explicit dry run and audit record."
                        ),
                    )
                )
            elif workflow.scheduled and not workflow.enabled:
                steps.append(
                    ScheduledWorkflowDryRunStep(
                        action="review-disabled-schedule",
                        repository=repository.repository,
                        workflow_path=workflow.workflow_path,
                        workflow_name=workflow.workflow_name,
                        reason=(
                            "Workflow is scheduled but disabled. Any future "
                            "reactivation should be confirmation-gated and "
                            "audited."
                        ),
                    )
                )
            elif workflow.schedule_source == "unavailable":
                steps.append(
                    ScheduledWorkflowDryRunStep(
                        action="verify-schedule-source",
                        repository=repository.repository,
                        workflow_path=workflow.workflow_path,
                        workflow_name=workflow.workflow_name,
                        reason=(
                            "GitHub did not return workflow YAML, so the cron "
                            "source could not be verified from the current scan."
                        ),
                    )
                )
    return ScheduledWorkflowDryRunPlan(steps=tuple(steps))


async def _cron_expressions_for_blob(
    organization: str,
    repo_name: str,
    gh_json: GhJson,
    sha: str,
) -> tuple[str, ...]:
    """Return the cron expressions for a workflow blob, fetching only unseen SHAs."""
    cached = _CRON_BY_BLOB_SHA.get(sha)
    if cached is not None:
        return cached
    blob = await gh_json(f"/repos/{organization}/{repo_name}/git/blobs/{sha}")
    content_b64 = blob.get("content", "") if isinstance(blob, dict) else ""
    content = base64.b64decode(content_b64 or "").decode("utf-8", errors="replace")
    crons = tuple(extract_cron_expressions(content))
    _CRON_BY_BLOB_SHA[sha] = crons
    return crons


async def _repo_workflow_entries(
    organization: str,
    repo_name: str,
    gh_json: GhJson,
) -> list[ScheduledWorkflowEntry]:
    """Build workflow entries for one repository from a fixed number of calls.

    Reads triggers from the ``.github/workflows`` contents listing (one call,
    which includes blob SHAs) instead of raw-fetching every workflow file, and
    fetches a blob only when its SHA has not been seen before (#1745).
    """
    workflows_payload = await gh_json(f"/repos/{organization}/{repo_name}/actions/workflows")
    workflows_data = workflows_payload.get("workflows", []) if isinstance(workflows_payload, dict) else []

    sha_by_path: dict[str, str] = {}
    listing_available = True
    try:
        listing_payload = await gh_json(f"/repos/{organization}/{repo_name}/contents/.github/workflows")
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        listing_available = False
        listing_payload = []

    if isinstance(listing_payload, list):
        for entry in listing_payload:
            if not isinstance(entry, dict):
                continue
            sha, path = entry.get("sha"), entry.get("path")
            if sha and path:
                sha_by_path[str(path)] = str(sha)
    else:
        listing_available = False

    workflow_entries: list[ScheduledWorkflowEntry] = []
    for workflow in workflows_data:
        if not isinstance(workflow, dict):
            continue

        workflow_path = str(workflow.get("path") or "").strip()
        workflow_name = str(workflow.get("name") or workflow_path or "").strip()
        workflow_id = int(workflow["id"]) if workflow.get("id") is not None else None
        state = str(workflow.get("state") or "").strip() or "unknown"
        enabled = state == "active"

        schedule_source = "unavailable"
        cron_expressions: tuple[str, ...] = ()
        sha = sha_by_path.get(workflow_path) if workflow_path else None
        if sha:
            try:
                cron_expressions = await _cron_expressions_for_blob(organization, repo_name, gh_json, sha)
                schedule_source = "blob"
            except Exception as e:  # noqa: BLE001
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                schedule_source = "unavailable"
        elif not listing_available:
            schedule_source = "unavailable"

        scheduled = bool(cron_expressions)
        latest_run: ScheduledWorkflowRunSnapshot | None = None
        if scheduled and workflow_id is not None:
            try:
                runs_payload = await gh_json(
                    f"/repos/{organization}/{repo_name}/actions/workflows/{workflow_id}/runs?per_page=1"
                )
                runs = runs_payload.get("workflow_runs", []) if isinstance(runs_payload, dict) else []
                if runs:
                    first_run = runs[0]
                    if isinstance(first_run, dict):
                        latest_run = _build_run_snapshot(first_run)
            except Exception as e:  # noqa: BLE001
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                latest_run = None

        workflow_entries.append(
            ScheduledWorkflowEntry(
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                workflow_path=workflow_path,
                state=state,
                enabled=enabled,
                scheduled=scheduled,
                schedule_source=schedule_source,
                cron_expressions=cron_expressions,
                latest_run=latest_run,
            )
        )

    return workflow_entries


async def _collect_repo_inventory(
    organization: str,
    repo: dict[str, Any],
    gh_json: GhJson,
    semaphore: asyncio.Semaphore,
) -> ScheduledWorkflowRepositoryInventory:
    repo_name = str(repo.get("name") or "").strip()
    default_branch = str(repo.get("default_branch") or "").strip() or None
    async with semaphore:
        workflow_entries = await _repo_workflow_entries(organization, repo_name, gh_json)
    return ScheduledWorkflowRepositoryInventory(
        repository=repo_name,
        archived=bool(repo.get("archived")),
        default_branch=default_branch,
        workflow_count=len(workflow_entries),
        scheduled_workflow_count=sum(1 for item in workflow_entries if item.scheduled),
        workflows=tuple(workflow_entries),
    )


async def collect_inventory(
    organization: str,
    gh_json: GhJson,
    *,
    repo_limit: int = 100,
    include_archived: bool = False,
) -> ScheduledWorkflowInventoryReport:
    """Collect a read-only org-wide inventory of scheduled workflows.

    Repositories are walked concurrently, bounded by ``_REPO_CONCURRENCY``,
    while the output preserves the order of ``repos_payload`` (#1745).
    """

    repos_payload = await gh_json(f"/orgs/{organization}/repos?per_page={repo_limit}&sort=updated&direction=desc")

    if not isinstance(repos_payload, list):
        repos_payload = []

    eligible_repos: list[dict[str, Any]] = []
    for repo in repos_payload:
        if not isinstance(repo, dict):
            continue
        if repo.get("archived") and not include_archived:
            continue
        if not str(repo.get("name") or "").strip():
            continue
        eligible_repos.append(repo)

    semaphore = asyncio.Semaphore(_REPO_CONCURRENCY)
    repositories = list(
        await asyncio.gather(
            *[_collect_repo_inventory(organization, repo, gh_json, semaphore) for repo in eligible_repos]
        )
    )
    total_scheduled = sum(repo.scheduled_workflow_count for repo in repositories)

    report = ScheduledWorkflowInventoryReport(
        organization=organization,
        generated_at=utc_now_iso(),
        repository_count=len(repositories),
        scheduled_workflow_count=total_scheduled,
        repositories=tuple(repositories),
        dry_run_plan=_build_dry_run_plan(repositories),
    )
    return report
