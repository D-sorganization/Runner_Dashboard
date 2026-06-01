"""Unit tests for the vendored Conductor enum constants (issue #810).

``backend/conductor_constants.py`` vendors the Conductor contract enum string
values so the dashboard never imports a sibling repo at runtime. These tests
pin the vendored values and assert they stay aligned with the authoritative
Conductor source (``Repository_Management/conductor/provider.py``) when it is
checked out. The endpoint-level drift assertions live in
``test_providers_registry.py``; this file guards the constants module directly
so the module-coverage invariant is satisfied and drift is caught even if the
registry router changes.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from conductor_constants import (  # noqa: E402
    AUTH_KINDS,
    CAPABILITIES,
    RESOURCES,
    TASK_CLASSES,
)


def test_constants_are_nonempty_string_tuples() -> None:
    for const in (CAPABILITIES, TASK_CLASSES, AUTH_KINDS, RESOURCES):
        assert isinstance(const, tuple)
        assert const, "constant tuple must be non-empty"
        assert all(isinstance(v, str) and v for v in const)


def test_constants_have_no_duplicates() -> None:
    for const in (CAPABILITIES, TASK_CLASSES, AUTH_KINDS, RESOURCES):
        assert len(const) == len(set(const))


def test_auth_kinds_match_endpoint_literals() -> None:
    # AuthMode values must equal the registry's auth_mode literal set.
    assert set(AUTH_KINDS) == {"none", "github_app", "api_key", "local"}


def test_resources_match_endpoint_literals() -> None:
    assert set(RESOURCES) == {"runner", "local"}


# ---------------------------------------------------------------------------
# Drift guard against the authoritative conductor source (skips if absent).
# ---------------------------------------------------------------------------
def _conductor_provider_path() -> Path | None:
    candidate = Path(__file__).resolve().parents[3] / "Repository_Management" / "conductor" / "provider.py"
    return candidate if candidate.exists() else None


def _enum_values(source: str, enum_name: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == enum_name:
            return {
                stmt.value.value
                for stmt in node.body
                if isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            }
    raise AssertionError(f"enum {enum_name} not found in conductor source")


@pytest.mark.parametrize(
    ("constant", "enum_name"),
    [
        (CAPABILITIES, "Capability"),
        (TASK_CLASSES, "TaskClass"),
        (AUTH_KINDS, "AuthMode"),
        (RESOURCES, "Resource"),
    ],
)
def test_vendored_values_match_conductor_source(constant: tuple[str, ...], enum_name: str) -> None:
    path = _conductor_provider_path()
    if path is None:
        pytest.skip("conductor source not checked out")
    expected = _enum_values(path.read_text(encoding="utf-8"), enum_name)
    assert set(constant) == expected
