"""Effort per dispatch kind and envelope validation (USE-1, issue #1865)."""

from __future__ import annotations

from typing import Any

import pytest
from ci_fix_dispatch import route_ci_fix
from dispatch_contract import CommandEnvelope, build_envelope
from dispatch_effort import (
    DEFAULT_EFFORT,
    DISPATCH_KIND_EFFORT,
    EFFORT_LEVELS,
    EXPAND_EPIC_CHILDREN,
    resolve_effort,
    validate_effort,
)


@pytest.mark.parametrize(
    ("kind", "effort"),
    [
        ("ci_fix:lint", "low"),
        ("ci_fix:test", "medium"),
        ("implementation", "medium"),
        ("design", "high"),
        (EXPAND_EPIC_CHILDREN, "low"),
    ],
)
def test_issue_mapping(kind: str, effort: str) -> None:
    assert resolve_effort(kind) == effort


def test_every_configured_kind_resolves_to_a_known_level() -> None:
    assert DISPATCH_KIND_EFFORT
    for kind, effort in DISPATCH_KIND_EFFORT.items():
        assert effort in EFFORT_LEVELS, kind
        assert resolve_effort(kind) == effort


@pytest.mark.parametrize("kind", ["", "unknown", "ci_fix:unknown", "  "])
def test_unknown_kinds_fall_back_to_medium(kind: str) -> None:
    assert DEFAULT_EFFORT == "medium"
    assert resolve_effort(kind) == "medium"


def test_kind_lookup_is_case_and_space_insensitive() -> None:
    assert resolve_effort("  CI_FIX:Lint ") == "low"


def test_explicit_override_wins_and_is_validated() -> None:
    assert resolve_effort("ci_fix:lint", override="high") == "high"
    with pytest.raises(ValueError, match="effort"):
        resolve_effort("ci_fix:lint", override="extreme")


def test_validate_effort_rejects_non_levels() -> None:
    assert validate_effort("low") == "low"
    for bad in ("", "LOW", "max", None, 3):
        with pytest.raises(ValueError, match="effort"):
            validate_effort(bad)


def test_resolve_effort_rejects_non_string_kind() -> None:
    with pytest.raises(TypeError):
        resolve_effort(None)  # type: ignore[arg-type]


# ─── CI-fix routing carries effort ───────────────────────────────────────────


def test_ci_fix_routes_carry_effort() -> None:
    assert route_ci_fix("lint").effort == "low"
    assert route_ci_fix("test").effort == "medium"
    assert route_ci_fix("lint", attempt_number=3, max_attempts=3).effort == "high"


# ─── Envelope contract ───────────────────────────────────────────────────────


def _envelope_dict(**extra: object) -> dict[str, object]:
    return {
        "action": "agents.dispatch.adhoc",
        "source": "dashboard",
        "target": "Repository_Management",
        "requested_by": "operator",
        **extra,
    }


def test_build_envelope_defaults_effort_to_medium() -> None:
    env = build_envelope(
        action="agents.dispatch.adhoc", source="dashboard", target="Repository_Management", requested_by="op"
    )
    assert env.effort == "medium"
    assert env.payload["effort"] == "medium"


def test_build_envelope_carries_effort_into_signed_payload() -> None:
    env = build_envelope(
        action="agents.dispatch.adhoc",
        source="dashboard",
        target="Repository_Management",
        requested_by="op",
        effort="low",
    )
    assert env.effort == "low"
    assert env.payload["effort"] == "low"
    assert env.verify_signature()


def test_build_envelope_rejects_unknown_effort() -> None:
    with pytest.raises(ValueError, match="effort"):
        build_envelope(
            action="agents.dispatch.adhoc",
            source="dashboard",
            target="Repository_Management",
            requested_by="op",
            effort="turbo",
        )


def test_from_dict_restores_effort_from_field_or_payload() -> None:
    assert CommandEnvelope.from_dict(_envelope_dict(effort="high")).effort == "high"
    assert CommandEnvelope.from_dict(_envelope_dict(payload={"effort": "low"})).effort == "low"
    assert CommandEnvelope.from_dict(_envelope_dict()).effort == "medium"
    assert CommandEnvelope.from_dict(_envelope_dict(effort="low")).to_dict()["effort"] == "low"


def test_from_dict_rejects_unknown_effort() -> None:
    with pytest.raises(ValueError, match="effort"):
        CommandEnvelope.from_dict(_envelope_dict(effort="ultra"))


def test_from_dict_rejects_unsigned_effort_that_differs_from_signed_payload() -> None:
    """Only ``payload`` is covered by the signature, so it is authoritative."""
    env = build_envelope(
        action="agents.dispatch.adhoc",
        source="dashboard",
        target="Repository_Management",
        requested_by="op",
        effort="low",
    )
    wire = env.to_dict()
    wire["effort"] = "high"  # tampered, unsigned top-level field
    with pytest.raises(ValueError, match="effort"):
        CommandEnvelope.from_dict(wire)


def test_from_dict_takes_effort_from_the_signed_payload() -> None:
    env = build_envelope(
        action="agents.dispatch.adhoc",
        source="dashboard",
        target="Repository_Management",
        requested_by="op",
        effort="low",
    )
    wire = env.to_dict()
    wire.pop("effort", None)
    restored = CommandEnvelope.from_dict(wire)
    assert restored.effort == "low"
    assert restored.verify_signature()


def _build(**kwargs: Any) -> CommandEnvelope:
    return build_envelope(
        action="agents.dispatch.adhoc",
        source="dashboard",
        target="Repository_Management",
        requested_by="op",
        **kwargs,
    )


def test_build_envelope_takes_effort_from_the_payload_and_round_trips() -> None:
    """A payload-only effort must not leave a mismatched top-level field."""
    env = _build(payload={"effort": "low"})
    assert env.effort == "low"
    assert env.payload["effort"] == "low"
    assert CommandEnvelope.from_dict(env.to_dict()).effort == "low"


def test_build_envelope_rejects_conflicting_effort_and_payload_effort() -> None:
    with pytest.raises(ValueError, match="effort"):
        _build(payload={"effort": "low"}, effort="high")


def test_build_envelope_defaults_effort_when_neither_is_given() -> None:
    env = _build()
    assert env.effort == DEFAULT_EFFORT
    assert CommandEnvelope.from_dict(env.to_dict()).effort == DEFAULT_EFFORT
