"""RoleSpec reads ``retry.failover_chain`` so RD matches the role file (#3064)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.role_spec import parse_role, read_and_parse_role_file
from staff.validator import validate_role_data

pytestmark = pytest.mark.unit

_SCHEMA = Path(__file__).resolve().parents[2] / "backend" / "staff" / "schema.json"
_CHAIN = [
    {"provider": "claude", "model": "claude-opus-5-5"},
    {"provider": "codex"},
    {"provider": "antigravity"},
    {"provider": "cursor-agent"},
]


def _role(**retry: Any) -> dict[str, Any]:
    return {
        "name": "rd-barb",
        "title": "RD Barb",
        "summary": "Persistent Barb seat.",
        "playbook": "docs/agents/rd-barb.md",
        "prompt_template": None,
        "instructions": "Monitor and shepherd.",
        "providers": ["claude", "codex", "antigravity", "cursor-agent"],
        "model": "claude-opus-5-5",
        "schedule": "*/30 * * * *",
        "window": None,
        "repos": ["Runner_Dashboard"],
        "scope": {},
        "budget": {"usd_per_run": 0, "usd_per_day": 0},
        "permissions": dict.fromkeys(("lease", "push_branch", "open_pr", "merge", "host_shell", "notify_user"), False),
        "reports_to": "barb",
        "holds": [],
        "surface": "dashboard",
        "retry": {"max_attempts": 4, **retry},
    }


def test_parse_role_reads_failover_chain_in_role_order() -> None:
    spec = parse_role(_role(failover_chain=_CHAIN), "rd-barb.yml")
    assert spec.valid, spec.errors
    assert spec.failover_chain == (
        FailoverStep("claude", "claude-opus-5-5"),
        FailoverStep("codex", None),
        FailoverStep("antigravity", None),
        FailoverStep("cursor-agent", None),
    )


def test_resolve_uses_role_chain_not_codex_first_default() -> None:
    spec = parse_role(_role(failover_chain=_CHAIN), "rd-barb.yml")
    assert [s.provider for s in resolve_failover_chain(spec)] == ["claude", "codex", "antigravity", "cursor-agent"]
    assert resolve_failover_chain(spec)[0].model == "claude-opus-5-5"


def test_role_without_chain_keeps_default_behaviour() -> None:
    spec = parse_role(_role(), "rd-barb.yml")
    assert spec.failover_chain == ()
    assert [s.provider for s in resolve_failover_chain(spec)][0] == "codex"


def test_chain_step_outside_role_providers_marks_role_invalid() -> None:
    data = _role(failover_chain=[{"provider": "ollama"}])
    spec = parse_role(data, "rd-barb.yml", errors=tuple(validate_role_data(data)))
    assert not spec.valid
    assert any("failover_chain[0]" in e for e in spec.errors)


def test_validator_accepts_failover_chain_key() -> None:
    assert validate_role_data(_role(failover_chain=_CHAIN)) == []


@pytest.mark.parametrize(
    "bad",
    [
        "claude",
        [],
        ["claude"],
        [{"model": "x"}],
        [{"provider": "claude", "model": ""}],
        [{"provider": "claude", "extra": 1}],
    ],
)
def test_validator_rejects_malformed_chain(bad: Any) -> None:
    problems = validate_role_data(_role(failover_chain=bad))
    assert any("failover_chain" in p for p in problems), problems


def test_to_dict_serialises_chain() -> None:
    spec = parse_role(_role(failover_chain=_CHAIN[:2]), "rd-barb.yml")
    assert spec.to_dict()["failover_chain"] == [
        {"provider": "claude", "model": "claude-opus-5-5"},
        {"provider": "codex", "model": None},
    ]


def test_role_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "rd-barb.yml"
    path.write_text(yaml.safe_dump(_role(failover_chain=_CHAIN)), encoding="utf-8")
    spec = read_and_parse_role_file(path)
    assert spec.valid, spec.errors
    assert spec.failover_chain[0] == FailoverStep("claude", "claude-opus-5-5")


def test_local_schema_mirrors_rm_failover_chain() -> None:
    retry = json.loads(_SCHEMA.read_text(encoding="utf-8"))["properties"]["retry"]["properties"]
    chain = retry["failover_chain"]
    assert chain["type"] == "array" and chain["minItems"] == 1
    assert chain["items"]["required"] == ["provider"]
    assert set(chain["items"]["properties"]) == {"provider", "model"}
