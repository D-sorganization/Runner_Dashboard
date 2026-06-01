"""Vendored copies of the Conductor contract enum string values (issue #810).

The dashboard never imports from a sibling repo at runtime (orthogonality /
decoupling rule, ``CLAUDE.md``). The Conductor orchestrator's authoritative
enums live in ``Repository_Management/conductor/provider.py`` as
:class:`~enum.StrEnum`s. To keep the shared ``/api/providers/registry``
contract aligned with that source *without* a cross-repo runtime import, we
vendor the string values here.

Drift is guarded by ``tests/api/test_providers_registry.py::TestConductorEnumDrift``,
which parses the conductor source (when checked out) and asserts these tuples
match exactly. If the conductor enums change, CI fails and these constants must
be updated in lockstep.

Source of truth (conductor ``provider.py`` as of contract version 1.0.0):

- ``TaskClass``  -> :data:`TASK_CLASSES`
- ``Capability`` -> :data:`CAPABILITIES`
- ``AuthMode``   -> :data:`AUTH_KINDS`
- ``Resource``   -> :data:`RESOURCES`
"""

from __future__ import annotations

#: Conductor ``Capability`` StrEnum values.
CAPABILITIES: tuple[str, ...] = (
    "code_edit",
    "code_review",
    "ci_fix",
    "test_fix",
    "refactor",
    "design",
    "security",
    "format",
    "lint_fix",
    "label",
    "comment",
    "doc",
)

#: Conductor ``TaskClass`` StrEnum values.
TASK_CLASSES: tuple[str, ...] = (
    "format",
    "lint_fix",
    "label",
    "comment",
    "doc_typo",
    "ci_fix",
    "test_fix",
    "refactor",
    "design",
    "security",
)

#: Conductor ``AuthMode`` StrEnum values.
AUTH_KINDS: tuple[str, ...] = (
    "none",
    "github_app",
    "api_key",
    "local",
)

#: Conductor ``Resource`` StrEnum values.
RESOURCES: tuple[str, ...] = (
    "runner",
    "local",
)

__all__ = ["AUTH_KINDS", "CAPABILITIES", "RESOURCES", "TASK_CLASSES"]
