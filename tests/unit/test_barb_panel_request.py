"""Panel request parsing and checks for the barbara relay (#3711)."""

from __future__ import annotations

import json

import pytest

from scripts import barb_panel_request as req

EXPERTS = [{"name": "A", "perspective": "a"}, {"name": "B", "perspective": "b"}, {"name": "C", "perspective": "c"}]
PRESETS = [
    {
        "id": "engineering-review",
        "experts": [
            {"name": "Architect", "perspective": "x", "provider": "claude", "model": None, "available": True},
            {"name": "Skeptic", "perspective": "y", "provider": "codex", "model": "gpt-x", "available": True},
            {"name": "Operator", "perspective": "z", "provider": "claude", "model": None, "available": True},
        ],
    },
    {"id": "frontier", "experts": [{"name": "Argon", "perspective": "p", "provider": "gemini", "available": False}]},
]


def _body(data: object) -> str:
    return f"Please run this.\n\n```json panel-request\n{json.dumps(data)}\n```\n"


def test_extracts_the_one_block() -> None:
    assert req.extract_block(_body({"topic": "t", "preset": "engineering-review"}))["topic"] == "t"


@pytest.mark.parametrize(
    "body",
    [None, "no block", _body({"topic": "t"}) + _body({"topic": "u"}), "```json panel-request\n[1]\n```", "x" * 20_001],
)
def test_bad_bodies_are_rejected(body: str | None) -> None:
    with pytest.raises(req.RequestRejected):
        req.extract_block(body)


def test_invalid_json_names_the_line() -> None:
    with pytest.raises(req.RequestRejected, match="line 1"):
        req.extract_block("```json panel-request\n{nope\n```")


@pytest.mark.parametrize(
    "data",
    [
        {"topic": "t", "preset": "engineering-review", "confirm_cost": True},
        {"topic": "t", "preset": "engineering-review", "moderator_provider": "codex"},
        {"preset": "engineering-review"},
        {"topic": "  ", "preset": "engineering-review"},
        {"topic": "t"},
        {"topic": "t", "preset": "engineering-review", "experts": EXPERTS},
        {"topic": "t", "preset": "../x"},
        {"topic": "t", "experts": [{"name": "A", "perspective": "a", "available": True}]},
        {"topic": "t", "experts": "A,B,C"},
    ],
)
def test_check_refuses_unsafe_or_ambiguous_requests(data: dict[str, object]) -> None:
    with pytest.raises(req.RequestRejected):
        req.check_request(data)


def test_preset_resolves_to_seats_without_availability_flag() -> None:
    request = req.check_request({"topic": "t", "preset": "engineering-review", "rounds": 2})
    body = req.hub_body(request, req.resolve_experts(request, PRESETS))
    assert body["rounds"] == 2 and "confirm_cost" not in body and "preset" not in body
    assert body["experts"][1] == {"name": "Skeptic", "perspective": "y", "provider": "codex", "model": "gpt-x"}
    assert all("available" not in seat for seat in body["experts"])


def test_preset_with_switched_off_seat_or_unknown_id_is_refused() -> None:
    with pytest.raises(req.RequestRejected, match="off"):
        req.resolve_experts(req.check_request({"topic": "t", "preset": "frontier"}), PRESETS)
    with pytest.raises(req.RequestRejected, match="unknown preset"):
        req.resolve_experts(req.check_request({"topic": "t", "preset": "nope"}), PRESETS)


def test_explicit_experts_pass_through() -> None:
    request = req.check_request({"topic": "t", "experts": EXPERTS})
    assert req.hub_body(request, req.resolve_experts(request, []))["experts"] == EXPERTS


def test_authors_default_includes_claude_and_codex_bots_and_env_overrides() -> None:
    default = req.parse_authors(None)
    assert {"dieterolson", "d-sorgclaudeagent[bot]", "d-sorgcodexagent[bot]", "codex-scheduled-tasks[bot]"} <= default
    assert req.parse_authors(" Alice , bob[bot] ") == {"alice", "bob[bot]"}


def test_fence_outgrows_backticks_inside() -> None:
    out = req.fenced("a ```` b")
    assert out.startswith("`````text\n") and out.endswith("\n`````")
