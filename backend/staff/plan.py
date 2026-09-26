"""Staff run planning and request contracts.

Precondition: Requests and plans are structured dataclasses with explicit LoD boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from staff import consolidation


@dataclass(frozen=True)
class RunRequest:
    """Validated, flat run request (built by the router from the POST body)."""

    role: str
    provider: str | None = None
    model: str | None = None
    repo: str = ""
    issue: int | None = None
    pr: int | None = None
    prompt: str = ""
    machine: str = "local"
    requested_by: str = ""
    on_behalf_of: str = ""
    thread_id: str = ""
    work_item_id: str = ""
    # PR-consolidation decision from ``staff.consolidation.decide`` (#1213); None when not applicable.
    consolidation: dict[str, Any] | None = None
    origin_node: str = ""

    @property
    def target_kind(self) -> str:
        return "issue" if self.issue else ("pr" if self.pr else "prompt")

    @property
    def target_ref(self) -> str:
        return f"#{self.issue}" if self.issue else (f"PR #{self.pr}" if self.pr else "")


@dataclass(frozen=True)
class RunPlan:
    """What a run *would* do; returned by dry runs and used by the worker."""

    role: str
    provider: str
    model: str | None
    repo: str
    target_kind: str
    target_ref: str
    operator_prompt: str
    prompt: str
    argv: list[str]
    branch: str
    lease_ritual: bool
    consolidation: dict[str, Any] | None = None
    focus: str = ""  # board priorities + directives for this repo (#1239)
    thread_id: str = ""
    work_item_id: str = ""
    origin_node: str = ""

    @property
    def strategy_mode(self) -> str:
        return str(self.consolidation.get("mode", "")) if self.consolidation else ""

    @property
    def consolidation_paragraph(self) -> str:
        return consolidation.prompt_paragraph(self.consolidation) if self.consolidation else ""

    @property
    def issue_number(self) -> str:
        return self.target_ref.lstrip("#") if self.target_kind == "issue" else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "repo": self.repo,
            "target_kind": self.target_kind,
            "target_ref": self.target_ref,
            "prompt": self.prompt,
            "argv": list(self.argv),
            "branch": self.branch,
            "lease_ritual": self.lease_ritual,
            "focus": self.focus,
            "thread_id": self.thread_id,
            "work_item_id": self.work_item_id,
            "consolidation": dict(self.consolidation) if self.consolidation else None,
        }
