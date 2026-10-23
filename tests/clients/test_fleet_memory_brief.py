"""Lesson entries and the one-call memory brief for Barb-role agents (#2559)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fleet_client import FleetAPIError, FleetArgumentError, FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # noqa: F401 - pytest fixtures
from fleet_tools import BY_CLI, BY_TOOL

FLEET = Path(__file__).resolve().parents[2] / "clients" / "fleet"
SCOPE = "barb:front-door"


@pytest.fixture
def client(fake_api: Any) -> FleetClient:
    return FleetClient(fake_api.url, token="tok-1", agent="claude", session="claude-1")


def _entry(i: str, kind: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"id": i, "entry_type": kind, "body": {"text": text}, "created_at": "2026-10-23T00:00:00Z", **extra}


def test_lesson_append_sends_provenance_and_defaults_source_run(client: FleetClient, fake_api: Any) -> None:
    client.memory_append("lesson", "grokbot", {"rule": "run ruff"}, repo="Runner_Dashboard")
    assert fake_api.last.path == "/api/v1/memory"
    assert fake_api.last.body == {
        "entry_type": "lesson",
        "source_product": "grokbot",
        "tags": [],
        "body": {"rule": "run ruff"},
        "repo": "Runner_Dashboard",
        "source_run": "claude-1",
    }
    client.memory_append("lesson", "grokbot", {"rule": "x"}, repo="R", source_run="run-9", visibility="barb-only")
    assert fake_api.last.body["source_run"] == "run-9"
    assert fake_api.last.body["visibility"] == "barb-only"
    assert "review_status" not in fake_api.last.body


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"body": {"rule": "x"}}, "repo"),
        ({"body": {"rule": "x" * 401}, "repo": "R"}, "400"),
        ({"body": {"rule": "x"}, "repo": "R", "visibility": "public"}, "visibility"),
    ],
)
def test_lesson_append_rejects_before_network(client: FleetClient, fake_api: Any, kwargs: dict, match: str) -> None:
    with pytest.raises(FleetArgumentError, match=match):
        client.memory_append("lesson", "grokbot", **kwargs)
    assert fake_api.requests == []


def test_lesson_only_fields_rejected_for_other_types(client: FleetClient, fake_api: Any) -> None:
    with pytest.raises(FleetArgumentError, match="lesson"):
        client.memory_append("task", "codex", {"a": 1}, repo="R")
    assert fake_api.requests == []


def test_memory_list_accepts_lesson_type(client: FleetClient, fake_api: Any) -> None:
    client.memory_list(entry_type="lesson")
    assert fake_api.last.query["type"] == "lesson"


def _canned(fake_api: Any) -> None:
    fake_api.respond("GET", "/api/v1/memory", 200, {"entries": [_entry("m1", "session_digest", "digest one")]})
    fake_api.respond(
        "GET",
        "/api/v1/staff/barb/turnover",
        200,
        {
            "count": 1,
            "entries": [
                {
                    "scope": SCOPE,
                    "author": "agent-grok",
                    "summary": "left off at step 3",
                    "next_actions": ["finish step 4"],
                    "created_at": "2026-10-22T00:00:00Z",
                }
            ],
        },
    )


def test_memory_brief_requests(client: FleetClient, fake_api: Any) -> None:
    _canned(fake_api)
    out = client.memory_brief(scope=SCOPE, limit=5)
    got = [(r.path, r.query) for r in fake_api.requests]
    assert ("/api/v1/memory", {"type": "lesson", "review_status": "promoted", "limit": "5"}) in got
    assert ("/api/v1/memory", {"type": "lesson", "review_status": "accepted", "limit": "5"}) in got
    assert ("/api/v1/memory", {"tag": SCOPE, "limit": "5"}) in got
    assert ("/api/v1/staff/barb/turnover", {"scope": SCOPE, "limit": "5"}) in got
    assert "left off at step 3" in out["brief"]
    assert "finish step 4" in out["brief"]
    assert "digest one" in out["brief"]
    assert out["scope"] == SCOPE


def test_memory_brief_without_scope_skips_tag_and_scope(client: FleetClient, fake_api: Any) -> None:
    _canned(fake_api)
    client.memory_brief()
    queries = [r.query for r in fake_api.requests if r.path == "/api/v1/memory"]
    assert all("tag" not in q for q in queries)
    turnover = [r.query for r in fake_api.requests if r.path.endswith("/turnover")]
    assert turnover == [{"limit": "10"}]


def test_memory_brief_lists_lessons_once_and_not_as_recent(client: FleetClient, fake_api: Any) -> None:
    lesson = {
        "id": "l1",
        "entry_type": "lesson",
        "body": {"rule": "always run ruff", "repo": "Runner_Dashboard"},
        "repo": "Runner_Dashboard",
        "created_at": "2026-10-23T00:00:00Z",
    }
    fake_api.respond("GET", "/api/v1/memory", 200, {"entries": [lesson, _entry("m2", "task", "do thing")]})
    brief = client.memory_brief()["brief"]
    assert brief.count("always run ruff") == 1  # deduped across accepted/promoted/recent
    assert "do thing" in brief


def test_memory_brief_is_size_capped(client: FleetClient, fake_api: Any) -> None:
    entries = [_entry(f"m{i}", "task", "y" * 900) for i in range(50)]
    fake_api.respond("GET", "/api/v1/memory", 200, {"entries": entries})
    out = client.memory_brief(limit=50)
    assert len(out["brief"]) <= 6000
    assert out["truncated"] is True


def test_memory_brief_degrades_per_section(client: FleetClient, fake_api: Any) -> None:
    _canned(fake_api)
    fake_api.respond("GET", "/api/v1/staff/barb/turnover", 403, {"detail": "nope"})
    out = client.memory_brief(scope=SCOPE)
    assert "turnover: unavailable (HTTP 403)" in out["brief"]
    assert "digest one" in out["brief"]


def test_memory_brief_all_sections_failing_raises(client: FleetClient, fake_api: Any) -> None:
    fake_api.respond("GET", "/api/v1/memory", 401, {"detail": "no"})
    fake_api.respond("GET", "/api/v1/staff/barb/turnover", 401, {"detail": "no"})
    with pytest.raises(FleetAPIError):
        client.memory_brief(scope=SCOPE)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [({"scope": "front-door"}, "scope"), ({"limit": 0}, "limit"), ({"limit": 51}, "limit")],
)
def test_memory_brief_validates_before_network(client: FleetClient, fake_api: Any, kwargs: dict, match: str) -> None:
    with pytest.raises(FleetArgumentError, match=match):
        client.memory_brief(**kwargs)
    assert fake_api.requests == []


def test_registered_as_tool_and_cli_with_untrusted_frame() -> None:
    cmd = BY_TOOL["fleet_memory_brief"]
    assert BY_CLI["memory brief"] is cmd
    assert cmd.untrusted is True
    assert set(cmd.properties) == {"scope", "limit"}
    append = BY_TOOL["fleet_memory_append"]
    assert "lesson" in append.properties["entry_type"]["enum"]
    for name in ("repo", "source_run", "visibility"):
        assert name in append.properties


def test_fleetctl_memory_brief_and_lesson_append(fake_api: Any) -> None:
    _canned(fake_api)
    env = {**os.environ, "FLEET_API_URL": fake_api.url, "FLEET_API_TOKEN": "tok-1", "FLEET_AGENT": "claude"}

    def run(*argv: str) -> tuple[int, str]:
        p = subprocess.run(  # noqa: S603
            [sys.executable, str(FLEET / "fleetctl.py"), *argv], capture_output=True, text=True, env=env, timeout=60
        )
        return p.returncode, p.stdout

    code, out = run("memory", "brief", "--scope", SCOPE, "--limit", "3")
    assert code == 0, out
    assert "left off at step 3" in out
    code, _ = run(
        "memory-append",
        "--entry-type",
        "lesson",
        "--source-product",
        "grokbot",
        "--repo",
        "R",
        "--body",
        json.dumps({"rule": "x"}),
    )
    assert code == 0
    assert fake_api.last.body["entry_type"] == "lesson"
    assert fake_api.last.body["repo"] == "R"
