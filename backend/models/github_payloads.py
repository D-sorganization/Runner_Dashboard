"""Typed Pydantic view-models for GitHub API response payloads.

These models cover only the fields the dashboard actually consumes.
Unknown fields are silently ignored (``extra="ignore"``), so adding new
GitHub API fields never breaks existing handlers.

Law-of-Demeter fix: instead of
    ``(run.get("repository") or {}).get("name", "")``
handlers receive a flat, typed object and can write
    ``run.repository.name`` or ``run.repository_name``.

Usage::

    from models.github_payloads import GhWorkflowRun

    raw_dict = await gh_api("/repos/org/repo/actions/runs")
    runs = [GhWorkflowRun.model_validate(r) for r in raw_dict.get("workflow_runs", [])]
    for run in runs:
        print(run.id, run.repository.name if run.repository else "")
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Shared sub-models
# --------------------------------------------------------------------------- #


class GhLabel(BaseModel):
    """A single runner label returned by the GitHub API."""

    id: int | None = None
    name: str = ""
    type: str | None = None

    model_config = {"extra": "ignore"}


class GhRepository(BaseModel):
    """Minimal repository shape consumed by dashboard routes.

    Fields kept to what the dashboard reads; everything else is silently
    ignored per ``extra="ignore"``.
    """

    id: int | None = None
    name: str = ""
    full_name: str = ""
    private: bool = False
    html_url: str = ""
    default_branch: str = "main"

    model_config = {"extra": "ignore"}


class GhActor(BaseModel):
    """GitHub user / actor reference."""

    id: int | None = None
    login: str = ""
    avatar_url: str = ""
    html_url: str = ""
    type: str = "User"

    model_config = {"extra": "ignore"}


# --------------------------------------------------------------------------- #
# Workflow run
# --------------------------------------------------------------------------- #


class GhWorkflowRun(BaseModel):
    """GitHub Actions workflow run as returned by the Workflow Runs API.

    Replaces patterns like::

        run.get("id")
        (run.get("repository") or {}).get("name", "")
        run.get("triggering_actor", {}).get("login")
    """

    id: int = 0
    name: str = ""
    status: str = ""
    conclusion: str | None = None
    created_at: str = ""
    updated_at: str = ""
    html_url: str = ""
    head_branch: str = ""
    head_sha: str = ""
    run_number: int = 0
    run_attempt: int = 1
    event: str = ""
    workflow_id: int | None = None
    workflow_url: str = ""
    display_title: str = ""
    actor: GhActor | None = None
    triggering_actor: GhActor | None = None
    repository: GhRepository | None = None

    model_config = {"extra": "ignore"}

    @property
    def repository_name(self) -> str:
        """Convenience: repo name without chaining .get()."""
        return self.repository.name if self.repository else ""

    @property
    def triggering_login(self) -> str:
        """Convenience: triggering actor login."""
        return self.triggering_actor.login if self.triggering_actor else ""

    @property
    def actor_login(self) -> str:
        """Convenience: actor login."""
        return self.actor.login if self.actor else ""


# --------------------------------------------------------------------------- #
# Workflow job
# --------------------------------------------------------------------------- #


class GhJob(BaseModel):
    """A single job within a workflow run (from /actions/runs/{id}/jobs)."""

    id: int = 0
    run_id: int = 0
    name: str = ""
    status: str = ""
    conclusion: str | None = None
    runner_id: int | None = None
    runner_name: str | None = None
    runner_group_id: int | None = None
    runner_group_name: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    html_url: str = ""
    labels: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @classmethod
    def from_api_dict(cls, data: dict) -> GhJob:
        """Construct from a raw GitHub API job dict.

        Normalises ``labels`` which may be a list of dicts or strings.
        """
        labels_raw = data.get("labels", [])
        if labels_raw and isinstance(labels_raw[0], dict):
            labels = [lbl.get("name", "") for lbl in labels_raw if isinstance(lbl, dict)]
        else:
            labels = [str(lbl) for lbl in labels_raw]
        return cls(**{**data, "labels": labels})


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


class GhRunner(BaseModel):
    """A self-hosted GitHub Actions runner.

    Replaces patterns like::

        runner["labels"][i]["name"]
        runner.get("status")
        runner.get("busy")
    """

    id: int = 0
    name: str = ""
    os: str = ""
    status: str = ""
    busy: bool = False
    labels: list[GhLabel] = Field(default_factory=list)
    accessed_at: str | None = None
    created_at: str | None = None

    model_config = {"extra": "ignore"}

    @property
    def label_names(self) -> list[str]:
        """Return label names as a flat list (replaces list-comp with .get())."""
        return [lbl.name for lbl in self.labels if lbl.name]

    @property
    def is_online(self) -> bool:
        return self.status == "online"
