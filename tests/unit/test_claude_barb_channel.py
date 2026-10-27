"""Claude planner <-> Barb channel: route-agnostic envelope, exchange rendering, fail-closed sends."""

from typing import Any

import pytest

from scripts import claude_barb_channel as ch

NOW = 1_800_000_000.0
QUESTION = {
    "kind": "question",
    "to": "barb",
    "summary": "Pick the memory route",
    "options": [{"label": "GitHub exchange repo", "recommended": True}, {"label": "Reopen ADR 0007"}],
    "recommendation_reason": "Keeps ADR 0007 intact and needs no new ingress.",
}


def _compose(**over: Any) -> tuple[dict[str, Any] | None, list[str]]:
    return ch.compose({**QUESTION, **over}, NOW)


def test_compose_seals_a_valid_envelope_from_the_planner() -> None:
    env, problems = _compose()
    assert problems == [] and env is not None
    assert env["from"] == ch.SENDER and env["hops"] == 0
    assert env["id"].startswith("m_") and env["correlation_id"].startswith("c_")
    assert env["recommended"] == "GitHub exchange repo"


def test_compose_is_stable_on_retry_within_the_dedupe_bucket() -> None:
    first, _ = _compose()
    again, _ = ch.compose(dict(QUESTION), NOW + 1)
    assert first is not None and again is not None and first["id"] == again["id"]


def test_any_barb_persona_and_dieter_are_valid_recipients() -> None:
    for to in ("barb", "rd-barb", "dieter", "*"):
        assert _compose(to=to)[1] == []


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"to": "someone-else"}, "to must be one of"),
        ({"from": "rd-barb"}, "from is always"),
        ({"hops": 4}, "hops"),
        ({"hops": "1"}, "hops"),
        ({"body": "token ghp_" + "a" * 36}, "secret"),
        ({"summary": "reach 192.168.1.20"}, "secret"),
        ({"recommendation_reason": ""}, "recommendation_reason"),
    ],
)
def test_compose_fails_closed_and_names_the_problem(over: dict[str, Any], needle: str) -> None:
    env, problems = _compose(**over)
    assert env is None and any(needle in p for p in problems)


def test_exchange_issue_round_trips_through_parse() -> None:
    env, _ = _compose()
    assert env is not None
    issue = ch.to_exchange_issue(env)
    assert issue["title"] == "[question] Pick the memory route"
    assert issue["labels"] == ["barb-inbox", "to:barb", "from:claude-planner"]
    parsed, problems = ch.parse_exchange_body(issue["body"])
    assert problems == [] and parsed == env


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("no envelope here", "marker"),
        (f"{ch.MARKER}\n```json\n{{not json\n```", "JSON"),
        (f"{ch.MARKER}\n```json\n[1]\n```", "object"),
        (f"{ch.MARKER}\n```json\n{{}}\n```\n```json\n{{}}\n```", "exactly one"),
    ],
)
def test_parse_rejects_malformed_bodies(body: str, needle: str) -> None:
    env, problems = ch.parse_exchange_body(body)
    assert env is None and any(needle in p for p in problems)


def test_parse_rejects_inbound_secrets_and_missing_ids() -> None:
    env, _ = _compose()
    assert env is not None
    leaky = ch.to_exchange_issue({**env, "body": "Bearer " + "x" * 30})["body"]
    assert any("secret" in p for p in ch.parse_exchange_body(leaky)[1])
    no_id = ch.to_exchange_issue({k: v for k, v in env.items() if k != "id"})["body"]
    assert any("id" in p for p in ch.parse_exchange_body(no_id)[1])


def test_inbound_reply_from_a_barb_is_accepted() -> None:
    env, _ = _compose()
    assert env is not None
    reply = {**env, "kind": "answer", "from": "rd-barb", "to": ch.SENDER, "task_state": "completed"}
    reply.pop("options"), reply.pop("recommended"), reply.pop("recommendation_reason")
    parsed, problems = ch.parse_exchange_body(ch.to_exchange_issue(reply)["body"])
    assert problems == [] and parsed is not None and parsed["kind"] == "answer"


class _Recorder:
    name = "recorder"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail = fail

    def deliver(self, envelope: dict[str, Any]) -> dict[str, Any]:
        if self.fail:
            raise OSError("network down")
        self.sent.append(envelope)
        return {"ok": True, "status": "sent", "ref": "issue-1"}


def test_send_is_dry_run_unless_the_channel_is_switched_on() -> None:
    rec = _Recorder()
    result = ch.send(dict(QUESTION), rec, NOW, live=False)
    assert result["status"] == "dry_run" and result["ok"] and rec.sent == []
    assert result["issue"]["title"].startswith("[question]")


def test_send_live_delivers_once_and_never_raises() -> None:
    rec = _Recorder()
    assert ch.send(dict(QUESTION), rec, NOW, live=True)["status"] == "sent" and len(rec.sent) == 1
    failed = ch.send(dict(QUESTION), _Recorder(fail=True), NOW, live=True)
    assert failed == {"ok": False, "status": "not_sent", "error": "deliver_failed", "transport": "recorder"}


def test_send_rejects_an_invalid_envelope_without_touching_the_transport() -> None:
    rec = _Recorder()
    result = ch.send({**QUESTION, "to": "nobody"}, rec, NOW, live=True)
    assert result["error"] == "invalid_envelope" and rec.sent == []


def test_live_switch_reads_only_the_kill_switch() -> None:
    assert ch.channel_live({}) is False
    assert ch.channel_live({"CLAUDE_BARB_CHANNEL": "off"}) is False
    assert ch.channel_live({"CLAUDE_BARB_CHANNEL": "on"}) is True


def test_exchange_transport_requires_a_repo_and_uses_the_injected_creator() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    env, _ = _compose()
    assert env is not None
    missing = ch.ExchangeTransport("", lambda repo, issue: "x").deliver(env)
    assert missing["error"] == "config_missing"
    transport = ch.ExchangeTransport("owner/exchange", lambda repo, issue: calls.append((repo, issue)) or "url-1")
    assert transport.deliver(env) == {"ok": True, "status": "sent", "ref": "url-1"}
    assert calls[0][0] == "owner/exchange" and calls[0][1]["labels"][0] == "barb-inbox"
