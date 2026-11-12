"""Bot-builder schema and grant-ceiling contract tests (RD #3103, PR slice 1)."""

from __future__ import annotations

import builtins
import dataclasses
from pathlib import Path

import pytest
from staff.bot_builder import (
    BUILDER_EDITOR_V1,
    GrantCeiling,
    load_grant_ceiling,
    parse_builder_role,
)
from staff.bot_builder.capabilities import KNOWN_CAPABILITIES, RD_CAPABILITIES

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
def ceiling() -> GrantCeiling:
    return load_grant_ceiling(CEILING_FILE)


def test_parse_minimal_role(ceiling: GrantCeiling) -> None:
    role = parse_builder_role(_minimal_role())
    assert role.name == "pilot-bot"
    assert role.capabilities == ("rd.staff_api",)
    assert "rd.staff_api" in ceiling.allowed_capabilities


def test_capability_vocabulary_includes_rd_categories() -> None:
    assert RD_CAPABILITIES <= KNOWN_CAPABILITIES


def test_hold_field_in_payload_fails() -> None:
    data = _minimal_role()
    data["holds"] = ["rd-2657"]
    with pytest.raises(ValueError, match="hold"):
        parse_builder_role(data)


def test_hold_capability_token_fails_at_parse() -> None:
    with pytest.raises(ValueError, match="hold"):
        parse_builder_role(_minimal_role(capabilities=["hold.grant"]))


def test_declared_triggers_only_on_schema() -> None:
    role = parse_builder_role(
        _minimal_role(triggers=[{"target_role": "night-watch", "event": "schedule_tick"}]),
    )
    assert len(role.triggers) == 1
    assert role.triggers[0].target_role == "night-watch"


def test_grant_ceiling_loads_frozen(ceiling: GrantCeiling) -> None:
    assert ceiling.approved_providers
    assert ceiling.allowed_capabilities
    with pytest.raises(dataclasses.FrozenInstanceError):
        ceiling.allowed_capabilities = frozenset()  # type: ignore[misc]


def test_builder_loader_has_no_write_path_to_grant_ceiling(
    ceiling: GrantCeiling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = CEILING_FILE.read_bytes()
    modes: list[str] = []
    real_open = builtins.open

    def spy(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if Path(file).resolve() == CEILING_FILE.resolve():
            modes.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    load_grant_ceiling(CEILING_FILE)
    parse_builder_role(_minimal_role())
    assert modes and all(set(m) <= {"r", "b", "t"} for m in modes)
    assert CEILING_FILE.read_bytes() == before


def test_v1_editor_is_form_only() -> None:
    assert BUILDER_EDITOR_V1 == "form"
