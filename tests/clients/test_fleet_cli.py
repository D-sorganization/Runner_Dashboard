"""fleetctl CLI tests (#1228): JSON on stdout, exit codes, schema-derived arguments."""

from __future__ import annotations

import json
from typing import Any

import fleetctl
import pytest
from fleet_client import FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # pytest fixtures
from fleet_tools import BY_CLI, BY_TOOL, COMMANDS


def _run(capsys: pytest.CaptureFixture[str], fake_api: Any, *argv: str) -> tuple[int, Any]:
    code = fleetctl.main(["--url", fake_api.url, *argv])
    return code, json.loads(capsys.readouterr().out)


def test_briefing(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, out = _run(capsys, fake_api, "briefing", "--repo", "Runner_Dashboard", "--agent", "grok")
    assert code == 0
    assert out["ok"] is True
    assert fake_api.last.path == "/api/coordination/briefing"
    assert fake_api.last.query == {"repo": "Runner_Dashboard", "agent": "grok"}
    assert fake_api.last.headers["x-requested-with"] == "XMLHttpRequest"


def test_dispatch_positional_role_and_dry_run(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(
        capsys, fake_api, "dispatch", "night-watch", "--repo", "Runner_Dashboard", "--issue", "4", "--dry-run"
    )
    assert code == 0
    assert fake_api.last.path == "/api/staff/night-watch/run"
    assert fake_api.last.body == {"repo": "Runner_Dashboard", "issue": 4, "machine": "auto", "dry_run": True}


def test_claim_uses_default_session(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(capsys, fake_api, "--as-session", "s-9", "claim", "--repo", "Runner_Dashboard", "--issue", "12")
    assert code == 0
    assert fake_api.last.body == {"repo": "Runner_Dashboard", "issue": 12, "session": "s-9"}


def test_as_agent_derives_an_agent_prefixed_session(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(capsys, fake_api, "--as-agent", "grok", "claim", "--repo", "Runner_Dashboard", "--issue", "12")
    assert code == 0
    assert fake_api.last.body["agent"] == "grok"
    assert fake_api.last.body["session"].startswith("grok-")


def test_mismatched_session_is_exit_2(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, out = _run(capsys, fake_api, "--as-agent", "grok", "--as-session", "codex-1", "inbox")
    assert code == 2
    assert "must start with 'grok-'" in out["message"]
    assert fake_api.requests == []


def test_release_claim_without_reason(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(capsys, fake_api, "release-claim", "--repo", "Runner_Dashboard", "--issue", "12", "--session", "s")
    assert code == 0
    assert fake_api.last.body == {"repo": "Runner_Dashboard", "issue": 12, "session": "s"}


def test_ack_command(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(capsys, fake_api, "ack", "--repo", "Runner_Dashboard", "--message-id", "m-1", "--session", "s")
    assert code == 0
    assert fake_api.last.path == "/api/coordination/messages/ack"


def test_register_presence_repeated_paths(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(
        capsys,
        fake_api,
        "register-presence",
        "--repo",
        "Runner_Dashboard",
        "--issue",
        "1228",
        "--branch",
        "feat/fleet-clients",
        "--session",
        "s-1",
        "--paths",
        "clients/",
        "--paths",
        "docs/agents/",
        "--goals",
        '{"ship": "clients"}',
    )
    assert code == 0
    assert fake_api.last.body["paths"] == ["clients/", "docs/agents/"]
    assert fake_api.last.body["goals"] == {"ship": "clients"}


def test_set_directives_json(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(capsys, fake_api, "set-directives", "--directives", '[{"text": "Ship #1192", "priority": 1}]')
    assert code == 0
    assert fake_api.last.method == "PUT"
    assert fake_api.last.body == {"directives": [{"text": "Ship #1192", "priority": 1, "repo": "*"}]}


def test_api_error_exit_1_with_error_json(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    fake_api.respond("POST", "/api/coordination/claims", 409, {"detail": "held by codex"})
    code, out = _run(capsys, fake_api, "claim", "--repo", "Runner_Dashboard", "--issue", "3", "--session", "s")
    assert code == 1
    assert out == {"error": "fleet_api_error", "status": 409, "body": {"detail": "held by codex"}}


def test_invalid_arguments_exit_2_nothing_sent(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, out = _run(capsys, fake_api, "dispatch", "night-watch", "--repo", "Runner_Dashboard")
    assert code == 2
    assert out["error"] == "invalid_arguments"
    assert fake_api.requests == []


def test_every_command_maps_to_a_client_method() -> None:
    for command in COMMANDS:
        assert callable(getattr(FleetClient, command.method)), command.method
        assert set(command.required) <= set(command.properties)
        assert set(command.positional) <= set(command.properties)
    assert len(BY_CLI) == len(COMMANDS)


def test_mcp_tool_set_is_the_contracted_list() -> None:
    assert set(BY_TOOL) == {
        "fleet_briefing",
        "fleet_priorities",
        "fleet_sessions",
        "fleet_inbox",
        "fleet_register_presence",
        "fleet_release_presence",
        "fleet_send_message",
        "fleet_ack_message",
        "fleet_check_claim",
        "fleet_claim_issue",
        "fleet_release_claim",
        "fleet_staff_summary",
        "fleet_staff_roster",
        "fleet_dispatch_role",
        "fleet_run_status",
        "fleet_directives",
        "staff_run_cancel",
        "staff_threads_list",
        "staff_thread_open",
        "staff_message_send",
        "staff_thread_read",
        "staff_thread_wait",
        "staff_work_items",
        "staff_approvals_list",
        "staff_approval_decide",
        "fleet_memory_context_pack",
        "fleet_memory_list",
        "fleet_memory_append",
        "fleet_memory_append_session_digest",
        "submit_proposal",
        "list_proposals",
        "fleet_query",
    }


def test_memory_commands(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(capsys, fake_api, "memory-context-pack")
    assert code == 0
    assert (fake_api.last.method, fake_api.last.path) == ("GET", "/api/v1/memory/context-pack")

    code, _ = _run(
        capsys, fake_api, "memory-list", "--entry-type", "task", "--tag", "Runner_Dashboard", "--limit", "25"
    )
    assert code == 0
    assert (fake_api.last.method, fake_api.last.path, fake_api.last.query) == (
        "GET",
        "/api/v1/memory",
        {"type": "task", "tag": "Runner_Dashboard", "limit": "25"},
    )

    code, _ = _run(
        capsys,
        fake_api,
        "memory-append",
        "--entry-type",
        "session_digest",
        "--source-product",
        "grokbot",
        "--body",
        '{"summary":"checkpoint"}',
    )
    assert code == 0
    assert (fake_api.last.method, fake_api.last.path, fake_api.last.body) == (
        "POST",
        "/api/v1/memory",
        {
            "entry_type": "session_digest",
            "source_product": "grokbot",
            "tags": [],
            "body": {"summary": "checkpoint"},
        },
    )


def test_library_fetch_accepts_source_work_and_page_locator(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    fake_api.respond(
        "GET",
        "/api/v1/library/fetch",
        200,
        {
            "node": "DeskComputer",
            "source_id": "desk-x9-library",
            "work_id": 3,
            "page": 7,
            "citation": "Verified citation",
            "cite_key": "[ref:DeskComputer:shared:w3:p4#sha256=12345678:p.6-8]",
            "file_sha256": "1" * 64,
            "work_sha256": "2" * 64,
            "text": "Cited passage",
        },
    )

    code, out = _run(
        capsys,
        fake_api,
        "library",
        "fetch",
        "desk-x9-library",
        "3",
        "--page",
        "7",
        "--run-id",
        "run-abc123",
    )

    assert code == 0
    assert out["cite_key"].startswith("[ref:DeskComputer:shared:")
    assert (fake_api.last.method, fake_api.last.path, fake_api.last.query) == (
        "GET",
        "/api/v1/library/fetch",
        {
            "source_id": "desk-x9-library",
            "work_id": "3",
            "page": "7",
            "scope": "local",
        },
    )
    assert fake_api.last.headers["x-staff-run-id"] == "run-abc123"


def test_library_fetch_locator_requires_page_before_network(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, out = _run(capsys, fake_api, "library", "fetch", "desk-x9-library", "3")

    assert code == 2
    assert "page" in out["message"]
    assert fake_api.requests == []


def test_library_fetch_keeps_canonical_citation_mode(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    citation = "[ref:DeskComputer:shared:w3:p4#sha256=12345678:p.6-8]"
    fake_api.respond(
        "GET",
        "/api/v1/library/fetch",
        200,
        {"cite_key": citation, "citation": "Verified citation", "text": "Cited passage"},
    )

    code, out = _run(capsys, fake_api, "library", "fetch", citation)

    assert code == 0
    assert out["results"][0]["cite_key"] == citation
    assert fake_api.last.query == {"cite": citation}


def test_library_fetch_rejects_invalid_source_id_before_network(
    capsys: pytest.CaptureFixture[str], fake_api: Any
) -> None:
    code, out = _run(capsys, fake_api, "library", "fetch", "../private", "3", "--page", "7")

    assert code == 2
    assert "source_id" in out["message"]
    assert fake_api.requests == []


def test_library_fetch_locator_accepts_explicit_node(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(
        capsys,
        fake_api,
        "library",
        "fetch",
        "desk-x9-library",
        "3",
        "--page",
        "7",
        "--node",
        "OGLaptop",
    )

    assert code == 0
    assert fake_api.last.query == {
        "source_id": "desk-x9-library",
        "work_id": "3",
        "page": "7",
        "scope": "local",
        "node": "OGLaptop",
    }


def test_library_search_scope_is_explicit_and_defaults_unchanged(
    capsys: pytest.CaptureFixture[str], fake_api: Any
) -> None:
    code, _ = _run(capsys, fake_api, "library", "search", "private topic", "--scope", "local")

    assert code == 0
    assert fake_api.last.path == "/api/v1/library/search"
    assert fake_api.last.query["scope"] == "local"

    code, _ = _run(capsys, fake_api, "library", "search", "shared topic")

    assert code == 0
    assert "scope" not in fake_api.last.query


def test_unified_library_search_scope_rejected_for_other_sources(
    capsys: pytest.CaptureFixture[str], fake_api: Any
) -> None:
    code, _ = _run(capsys, fake_api, "query", "--source", "library", "--query", "private topic", "--scope", "local")

    assert code == 0
    assert fake_api.last.query["scope"] == "local"

    code, out = _run(capsys, fake_api, "query", "--source", "knowledge", "--query", "topic", "--scope", "local")

    assert code == 2
    assert "only supported for library search" in out["message"]
