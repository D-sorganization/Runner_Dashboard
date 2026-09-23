"""FleetClient contract tests against a local fake HTTP API (#1228)."""

from __future__ import annotations

from typing import Any

import pytest
from fleet_client import DEFAULT_URL, FleetAPIError, FleetArgumentError, FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # pytest fixtures


@pytest.fixture
def client(fake_api: Any) -> FleetClient:
    return FleetClient(fake_api.url, token="tok-123", agent="claude", session="claude-1")


# (method name, kwargs, expected HTTP method, expected path, expected query, expected body)
ENDPOINTS: list[tuple[str, dict[str, Any], str, str, dict[str, str], Any]] = [
    ("staff_summary", {}, "GET", "/api/staff/summary", {}, None),
    ("staff_roster", {}, "GET", "/api/staff/roster", {}, None),
    ("staff_board", {"local": True}, "GET", "/api/staff/board", {"local": "true"}, None),
    (
        "staff_runs",
        {"limit": 5, "role": "night-watch"},
        "GET",
        "/api/staff/runs",
        {"limit": "5", "role": "night-watch"},
        None,
    ),
    ("run", {"run_id": "run-905a8b3586a7"}, "GET", "/api/staff/runs/run-905a8b3586a7", {"events": "200"}, None),
    ("staff_schedule", {}, "GET", "/api/staff/schedule", {}, None),
    ("holds", {}, "GET", "/api/staff/holds", {}, None),
    ("usage", {"group": "role"}, "GET", "/api/staff/usage", {"group": "role"}, None),
    ("cancel", {"run_id": "run-1"}, "POST", "/api/staff/runs/run-1/cancel", {}, {}),
    ("sessions", {"repo": "Runner_Dashboard"}, "GET", "/api/coordination/sessions", {"repo": "Runner_Dashboard"}, None),
    ("inbox", {}, "GET", "/api/coordination/inbox", {"session": "claude-1"}, None),
    (
        "release_presence",
        {"repo": "Runner_Dashboard"},
        "POST",
        "/api/coordination/presence/release",
        {},
        {"session": "claude-1", "repo": "Runner_Dashboard"},
    ),
    (
        "send_message",
        {"repo": "Runner_Dashboard", "to": "codex-7", "text": " hi "},
        "POST",
        "/api/coordination/messages",
        {},
        {"session": "claude-1", "repo": "Runner_Dashboard", "to": "codex-7", "text": "hi"},
    ),
    (
        "send_message",
        {"repo": "Runner_Dashboard", "to": "*", "text": "heads up"},
        "POST",
        "/api/coordination/messages",
        {},
        {"session": "claude-1", "repo": "Runner_Dashboard", "to": "*", "text": "heads up"},
    ),
    (
        "ack",
        {"repo": "Runner_Dashboard", "message_id": "m-9"},
        "POST",
        "/api/coordination/messages/ack",
        {},
        {"session": "claude-1", "repo": "Runner_Dashboard", "message_id": "m-9"},
    ),
    (
        "check_claim",
        {"repo": "Runner_Dashboard", "issue": 7},
        "GET",
        "/api/coordination/claims",
        {"repo": "Runner_Dashboard", "issue": "7"},
        None,
    ),
    (
        "claim",
        {"repo": "Runner_Dashboard", "issue": 7, "intent": "fix"},
        "POST",
        "/api/coordination/claims",
        {},
        {"repo": "Runner_Dashboard", "issue": 7, "agent": "claude", "session": "claude-1", "intent": "fix"},
    ),
    (
        "claim",
        {"repo": "Runner_Dashboard", "issue": 7},
        "POST",
        "/api/coordination/claims",
        {},
        {"repo": "Runner_Dashboard", "issue": 7, "agent": "claude", "session": "claude-1"},
    ),
    (
        "release_claim",
        {"repo": "Runner_Dashboard", "issue": 7},
        "POST",
        "/api/coordination/claims/release",
        {},
        {"repo": "Runner_Dashboard", "issue": 7, "agent": "claude", "session": "claude-1"},
    ),
    (
        "release_claim",
        {"repo": "Runner_Dashboard", "issue": 7, "reason": "PR #8"},
        "POST",
        "/api/coordination/claims/release",
        {},
        {"repo": "Runner_Dashboard", "issue": 7, "agent": "claude", "session": "claude-1", "reason": "PR #8"},
    ),
    (
        "briefing",
        {"repo": "Runner_Dashboard"},
        "GET",
        "/api/coordination/briefing",
        {"repo": "Runner_Dashboard", "agent": "claude"},
        None,
    ),
    ("priorities", {}, "GET", "/api/priorities", {}, None),
    ("meetings", {}, "GET", "/api/priorities/meetings", {}, None),
    ("meeting", {"date": "2026-09-22"}, "GET", "/api/priorities/meetings/2026-09-22", {}, None),
    ("directives", {}, "GET", "/api/priorities/directives", {}, None),
]


@pytest.mark.parametrize(
    ("name", "kwargs", "method", "path", "query", "body"),
    ENDPOINTS,
    ids=[f"{e[0]}-{i}" for i, e in enumerate(ENDPOINTS)],
)
def test_endpoint_shapes(
    client: FleetClient,
    fake_api: Any,
    name: str,
    kwargs: dict[str, Any],
    method: str,
    path: str,
    query: dict,
    body: Any,
) -> None:
    result = getattr(client, name)(**kwargs)
    rec = fake_api.last
    assert (rec.method, rec.path, rec.query, rec.body) == (method, path, query, body)
    assert result["ok"] is True


def test_every_request_carries_csrf_and_bearer(client: FleetClient, fake_api: Any) -> None:
    client.staff_summary()
    client.claim("Runner_Dashboard", 3)
    for rec in fake_api.requests:
        assert rec.headers["x-requested-with"] == "XMLHttpRequest"
        assert rec.headers["authorization"] == "Bearer tok-123"
        assert rec.headers["accept"] == "application/json"
    assert fake_api.last.headers["content-type"] == "application/json"


def test_no_token_means_no_authorization_header(fake_api: Any) -> None:
    FleetClient(fake_api.url).priorities()
    assert "authorization" not in fake_api.last.headers
    assert fake_api.last.headers["x-requested-with"] == "XMLHttpRequest"


def test_environment_configuration(fake_api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_API_URL", fake_api.url + "/")
    monkeypatch.setenv("FLEET_API_TOKEN", "env-tok")
    monkeypatch.setenv("FLEET_AGENT", "gemini")
    monkeypatch.setenv("FLEET_SESSION", "gemini-1")
    monkeypatch.setenv("FLEET_API_TIMEOUT", "4")
    client = FleetClient()
    assert (client.base_url, client.timeout) == (fake_api.url, 4.0)
    client.register_presence("Runner_Dashboard", issue=12, branch="feat/x", paths=["clients/"], ttl_hours=2)
    rec = fake_api.last
    assert rec.headers["authorization"] == "Bearer env-tok"
    assert rec.path == "/api/coordination/presence"
    assert rec.body == {
        "agent": "gemini",
        "session": "gemini-1",
        "repo": "Runner_Dashboard",
        "issue": 12,
        "branch": "feat/x",
        "paths": ["clients/"],
        "ttl_hours": 2,
    }


def test_default_url() -> None:
    assert FleetClient().base_url == DEFAULT_URL == "http://127.0.0.1:8321"


def test_dispatch_body_and_defaults(client: FleetClient, fake_api: Any) -> None:
    client.dispatch("night-watch", repo="Runner_Dashboard", prompt="sweep", dry_run=True)
    rec = fake_api.last
    assert (rec.method, rec.path) == ("POST", "/api/staff/night-watch/run")
    assert rec.body == {"repo": "Runner_Dashboard", "prompt": "sweep", "machine": "auto", "dry_run": True}


def test_set_directives_normalises(client: FleetClient, fake_api: Any) -> None:
    client.set_directives([{"text": " Ship #1192 ", "priority": 1, "repo": "Runner_Dashboard"}])
    rec = fake_api.last
    assert (rec.method, rec.path) == ("PUT", "/api/priorities/directives")
    assert rec.body == {"directives": [{"text": "Ship #1192", "priority": 1, "repo": "Runner_Dashboard"}]}


def test_set_directives_passes_version(client: FleetClient, fake_api: Any) -> None:
    client.set_directives([{"text": "Ship"}], version="abc123")
    assert fake_api.last.body == {"directives": [{"text": "Ship", "priority": 3, "repo": "*"}], "version": "abc123"}


def test_client_limits_mirror_the_server_models() -> None:
    import re  # noqa: PLC0415

    import fleet_client as fc  # noqa: PLC0415
    from coordination import models  # noqa: PLC0415
    from priorities import directives  # noqa: PLC0415
    from routers import priorities as prio_router  # noqa: PLC0415

    assert fc.LIMITS.max_directives == prio_router.MAX_DIRECTIVES
    assert fc.LIMITS.max_directive_text == directives.MAX_TEXT
    assert fc.LIMITS.max_message_text == models.MAX_TEXT
    assert fc.LIMITS.max_reason == models.ClaimReleaseBody.model_fields["reason"].metadata[-1].max_length
    assert fc.PATTERNS.branch.pattern == models.BRANCH_PATTERN
    assert fc.PATTERNS.agent.pattern == models.AGENT_PATTERN
    assert fc.PATTERNS.repo.pattern == models.REPO_PATTERN
    assert fc.PATTERNS.intent.pattern == models.INTENT_PATTERN
    assert re.fullmatch(fc.PATTERNS.session, "claude-rd.2026")


def test_http_error_raises_with_status_and_body(client: FleetClient, fake_api: Any) -> None:
    fake_api.respond("POST", "/api/coordination/claims", 409, {"detail": {"held": True, "agent": "codex"}})
    with pytest.raises(FleetAPIError) as info:
        client.claim("Runner_Dashboard", 5)
    assert info.value.status == 409
    assert info.value.body == {"detail": {"held": True, "agent": "codex"}}
    assert info.value.to_dict()["status"] == 409


def test_unreachable_server_is_status_zero() -> None:
    client = FleetClient("http://127.0.0.1:9", timeout=2)
    with pytest.raises(FleetAPIError) as info:
        client.priorities()
    assert info.value.status == 0
    assert info.value.body["error"] == "unreachable"


BAD_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("dispatch", {"role": "night-watch", "repo": "Runner_Dashboard"}),  # no issue/pr/prompt
    ("dispatch", {"role": "night-watch", "repo": "D-sorganization/Runner_Dashboard", "issue": 1}),  # owner prefix
    ("dispatch", {"role": "../admin", "issue": 1}),
    ("dispatch", {"role": "x", "prompt": "p" * 20001}),
    ("claim", {"repo": "../etc", "issue": 1}),
    ("claim", {"repo": "Runner_Dashboard", "issue": 0}),
    ("claim", {"repo": "Runner_Dashboard", "issue": True}),
    ("send_message", {"repo": "Runner_Dashboard", "to": "x", "text": "   "}),
    ("send_message", {"repo": "Runner_Dashboard", "to": "x", "text": "t" * 4001}),
    ("meeting", {"date": "yesterday"}),
    ("run", {"run_id": "../../etc"}),
    ("usage", {"group": "machine"}),
    ("staff_runs", {"limit": 0}),
    ("staff_runs", {"status": "weird"}),
    ("set_directives", {"directives": [{"text": "x", "priority": 9}]}),
    ("set_directives", {"directives": [{"text": "x", "colour": "red"}]}),
    ("register_presence", {"repo": "Runner_Dashboard", "issue": 1, "branch": "b", "ttl_hours": 0}),
    ("register_presence", {"repo": "Runner_Dashboard", "issue": 1, "branch": "b", "ttl_hours": 9}),
    ("register_presence", {"repo": "Runner_Dashboard", "issue": 0, "branch": "b"}),
    ("register_presence", {"repo": "Runner_Dashboard", "issue": 1, "branch": "-rf"}),
    ("register_presence", {"repo": "Runner_Dashboard", "issue": 1, "branch": "a b"}),
    ("register_presence", {"repo": "Runner_Dashboard", "issue": 1, "branch": "b", "session": "has space"}),
    ("register_presence", {"repo": "Runner_Dashboard", "issue": 1, "branch": "b", "session": "s" * 129}),
    ("claim", {"repo": "Runner_Dashboard", "issue": 1, "intent": "fix\nrm -rf"}),
    ("claim", {"repo": "Runner_Dashboard", "issue": 1, "intent": "i" * 201}),
    ("claim", {"repo": "Runner_Dashboard", "issue": 1, "intent": "-flag"}),
    ("release_claim", {"repo": "Runner_Dashboard", "issue": 1, "reason": "done\rnow"}),
    ("release_claim", {"repo": "Runner_Dashboard", "issue": 1, "reason": "r" * 301}),
    ("send_message", {"repo": "Runner_Dashboard", "to": "**", "text": "t"}),
    ("set_directives", {"directives": [{"text": "x"}] * 101}),
    ("set_directives", {"directives": [{"text": "Finish:\n- [ ] A"}]}),
    ("set_directives", {"directives": [{"text": "x", "set_by": "dieter"}]}),
    ("set_directives", {"directives": [], "version": ""}),
]


@pytest.mark.parametrize(("name", "kwargs"), BAD_CALLS)
def test_preconditions_reject_before_sending(
    client: FleetClient, fake_api: Any, name: str, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(FleetArgumentError):
        getattr(client, name)(**kwargs)
    assert fake_api.requests == []


def test_session_required_when_not_configured(fake_api: Any) -> None:
    with pytest.raises(FleetArgumentError, match="session"):
        FleetClient(fake_api.url).inbox()
    assert fake_api.requests == []


def test_invalid_base_url_rejected() -> None:
    with pytest.raises(FleetArgumentError):
        FleetClient("file:///etc/passwd")


# ---------------------------------------------------------------- session convention (#1243 / #1245)


def test_default_session_is_agent_host_date() -> None:
    import datetime as dt  # noqa: PLC0415

    import fleet_client as fc  # noqa: PLC0415

    day = dt.date(2026, 9, 23)
    assert fc.default_session("claude", "DeskComputer.tail1234.ts.net", day) == "claude-DeskComputer-20260923"
    assert fc.default_session("codex", "my host!", day) == "codex-my-host--20260923"
    long = fc.default_session("grok", "h" * 300, day)
    assert len(long) <= 128 and long.startswith("grok-") and fc.PATTERNS.session.match(long)


def test_session_defaults_from_agent_when_unset(fake_api: Any) -> None:
    client = FleetClient(fake_api.url, agent="gemini")
    client.inbox()
    session = fake_api.last.query["session"]
    import fleet_client as fc  # noqa: PLC0415

    assert session.startswith("gemini-") and fc.PATTERNS.session.match(session)


def test_env_agent_derives_session(fake_api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_AGENT", "codex")
    FleetClient(fake_api.url).claim("Runner_Dashboard", 4)
    assert fake_api.last.body["session"].startswith("codex-")
    assert fake_api.last.body["agent"] == "codex"


def test_per_call_agent_derives_its_own_session(fake_api: Any) -> None:
    FleetClient(fake_api.url).claim("Runner_Dashboard", 4, agent="grok")
    assert fake_api.last.body["session"].startswith("grok-")


@pytest.mark.parametrize(
    ("kwargs", "call"),
    [
        ({"agent": "claude", "session": "codex-1"}, lambda c: c.inbox()),
        ({"agent": "claude", "session": "claude-1"}, lambda c: c.claim("Runner_Dashboard", 1, agent="codex")),
        ({"agent": "claude"}, lambda c: c.send_message("Runner_Dashboard", "*", "hi", session="claudeX")),
    ],
)
def test_explicit_session_must_carry_the_agent_prefix(fake_api: Any, kwargs: dict[str, Any], call: Any) -> None:
    with pytest.raises(FleetArgumentError, match="must start with"):
        call(FleetClient(fake_api.url, **kwargs))
    assert fake_api.requests == []


def test_without_an_agent_any_valid_session_is_accepted(fake_api: Any) -> None:
    FleetClient(fake_api.url, session="anything-1").inbox()
    assert fake_api.last.query == {"session": "anything-1"}
