"""Planner output model for Code Requests (CR-4, #1285).

The planner agent returns one JSON document (optionally inside a fenced block). It is
parsed into ``PlanDraft`` here and checked semantically by ``plan_validator``; nothing
it says is filed on GitHub until both pass and, when the profile requires it, an
operator approves.

Vocabularies are shared with the fleet: ``TIER_LABELS`` are the dispatch tiers (BP-4),
``TASK_CLASS_LABELS`` the plain labels the Conductor routes on.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

TIER_LABELS = frozenset({"tier:ollama", "tier:cli", "tier:strong"})
COMPLEXITIES = frozenset({"trivial", "small", "medium", "large"})
TASK_CLASS_LABELS = frozenset(
    {"feature", "bug", "test", "refactor", "security", "docs", "ci", "design", "lint", "format"}
)
EXTERNAL_REF = re.compile(r"^(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#\d+$")

_FENCED_JSON = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


class PlanFormatError(ValueError):
    """The planner output is not a JSON plan document."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ValidationCommand(_Strict):
    command: str = Field(min_length=1)
    expect: str = Field(min_length=1)


class FileScope(_Strict):
    allowed: list[str] = Field(min_length=1)
    forbidden: list[str] = Field(default_factory=list)


class DuplicateCitation(_Strict):
    number: int = Field(ge=1)
    title: str = ""
    reason: str = Field(min_length=1)


class PlanChild(_Strict):
    """One execution-ready child issue."""

    key: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1)
    execution_instructions: list[str] = Field(min_length=1)
    file_scope: FileScope
    acceptance_criteria: list[str] = Field(min_length=1)
    validation_commands: list[ValidationCommand] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(min_length=1)
    tier: str
    complexity: str
    task_class: str
    key_decisions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(min_length=1)

    def local_dependencies(self) -> list[str]:
        """Dependencies on sibling children (keys), as opposed to existing issue refs."""
        return [d for d in self.dependencies if not EXTERNAL_REF.match(d)]

    def external_dependencies(self) -> list[str]:
        return [d for d in self.dependencies if EXTERNAL_REF.match(d)]


class PlanEpic(_Strict):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)


class PlanDraft(_Strict):
    """A complete plan: one epic and its children."""

    epic: PlanEpic
    children: list[PlanChild] = Field(min_length=1, max_length=30)
    duplicates: list[DuplicateCitation] = Field(default_factory=list)


class DependencyCycleError(ValueError):
    """Children depend on each other in a cycle."""


def extract_plan_json(text: str) -> dict[str, Any]:
    """Return the JSON object in ``text``: a fenced block if present, else the whole text."""
    for candidate in [*_FENCED_JSON.findall(text), text]:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    raise PlanFormatError("Planner output must be one JSON object (optionally in a ```json fenced block)")


def parse_plan_output(text: str) -> PlanDraft:
    """Parse planner output into a ``PlanDraft``; raises ``PlanFormatError`` or pydantic errors."""
    return PlanDraft.model_validate(extract_plan_json(text))


def compute_waves(children: list[PlanChild]) -> list[list[str]]:
    """Layer children into dependency waves (Kahn); raises ``DependencyCycleError``.

    Precondition: every local dependency names a sibling key.
    """
    deps = {c.key: set(c.local_dependencies()) for c in children}
    order = [c.key for c in children]
    waves: list[list[str]] = []
    done: set[str] = set()
    while len(done) < len(order):
        wave = [k for k in order if k not in done and deps[k] <= done]
        if not wave:
            stuck = sorted(k for k in order if k not in done)
            raise DependencyCycleError(f"Dependency cycle among children: {', '.join(stuck)}")
        waves.append(wave)
        done.update(wave)
    return waves
