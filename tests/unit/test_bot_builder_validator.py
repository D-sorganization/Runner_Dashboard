"""Bot-builder grant ceiling and role validator contract tests (RD #3103)."""

from __future__ import annotations

import builtins
import dataclasses
from pathlib import Path

import pytest

from staff.bot_builder import (
    BuilderRoleDefinition,
    ValidationResult,
    load_grant_ceiling,
    parse_builder_role,
    validate_builder_role,
)

CEILING_FILE = Path(__file__).resolve().parents[2] / "config" / "bot_builder_grant_ceiling.json"


def _minimal_role(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": 1,
        "name": "pilot-bot",
        "persona": "Helpful assistant for fleet status.",
        "instructions": "Answer briefly; refuse unsafe requests.",
        "skills": ["status-summary"],
        "model": "routine-default",
        "providers": ["sub-cli-alpha"],
        "schedule": None,
        "triggers": [],
        "host": "grokbot-first",
        "enabled": False,
        "capabilities": ["rd.staff_api"],
        "private_context_ref": None,
        "household_data_opt_in": False,
    }
    base.update(overrides)
    return base


@pytest.fixture
def ceiling():
    return load_grant_ceiling(CEILING_FILE)


def test_within_ceiling_role_passes(ceiling) -> None:
    role = parse_builder_role(_minimal_role())
    result = validate_builder_role(role, ceiling)
    assert result == ValidationResult(ok=True, reasons=())


def test_above_ceiling_capability_fails_with_category_reason(ceiling) -> None:
    role = parse_builder_role(_minimal_role(capabilities=["rd.staff_api", "grok.credential_vault"]))
    result = validate_builder_role(role, ceiling)
    assert not result.ok
    assert any("capability" in r and "ceiling" in r for r in result.reasons)


def test_hold_field_in_payload_fails(ceiling) -> None:
    role = parse_builder_role(_minimal_role())
    data = _minimal_role()
    data["holds"] = ["rd-2657"]
    with pytest.raises(ValueError, match="hold"):
        parse_builder_role(data)


def test_hold_capability_token_fails(ceiling) -> None:
    with pytest.raises(ValueError, match="hold"):
        parse_builder_role(_minimal_role(capabilities=["hold.grant"]))


def test_unapproved_provider_fails(ceiling) -> None:
    role = parse_builder_role(_minimal_role(providers=["pay-per-use-api"]))
    result = validate_builder_role(role, ceiling)
    assert not result.ok
    assert any("provider" in r for r in result.reasons)


def test_household_capability_without_opt_in_fails(ceiling) -> None:
    role = parse_builder_role(_minimal_role(capabilities=["household.mail"]))
    result = validate_builder_role(role, ceiling)
    assert not result.ok
    assert any("household" in r for r in result.reasons)


def test_household_capability_with_opt_in_passes(ceiling) -> None:
    role = parse_builder_role(
        _minimal_role(capabilities=["household.mail"], household_data_opt_in=True),
    )
    result = validate_builder_role(role, ceiling)
    assert result.ok


def test_builder_has_no_write_path_to_grant_ceiling(ceiling, monkeypatch: pytest.MonkeyPatch) -> None:
    before = CEILING_FILE.read_bytes()
    modes: list[str] = []
    real_open = builtins.open

    def spy(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if Path(file).resolve() == CEILING_FILE.resolve():
            modes.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    role = parse_builder_role(_minimal_role())
    validate_builder_role(role, load_grant_ceiling(CEILING_FILE))
    assert modes and all(set(m) <= {"r", "b", "t"} for m in modes)
    assert CEILING_FILE.read_bytes() == before
    with pytest.raises(dataclasses.FrozenInstanceError):
        ceiling.allowed_capabilities = ()  # type: ignore[misc]


def test_v1_editor_is_form_only() -> None:
    from staff.bot_builder import BUILDER_EDITOR_V1

    assert BUILDER_EDITOR_V1 == "form"
