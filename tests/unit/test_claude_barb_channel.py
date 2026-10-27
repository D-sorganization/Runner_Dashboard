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


def test_id_survives_a_late_retry_and_only_a_new_key_or_thread_changes_it() -> None:
    late, keyed = ch.compose(dict(QUESTION), NOW + 86_400)[0], ch.compose(dict(QUESTION), NOW, idempotency_key="w44")[0]
    ids = [(e or {}).get("id") for e in (late, _compose()[0], keyed, _compose(correlation_id=f"c_{'a' * 24}")[0])]
    assert None not in ids and ids[0] == ids[1] and len(set(ids)) == 3


def test_reply_keeps_the_thread_counts_a_hop_and_stops_at_the_limit() -> None:
    inbound, _ = _compose()
    assert inbound is not None
    asked = {**inbound, "kind": "answer", "from": "rd-barb", "to": ch.SENDER, "hops": 1, "task_state": "completed"}
    assert ch.parse_exchange_body(ch.to_exchange_issue(asked)["body"])[1] == []  # an inbound answer parses
    env, problems = ch.reply(asked, {"kind": "update", "summary": "Noted"}, NOW)
    assert problems == [] and env is not None and env["to"] == "rd-barb" and env["hops"] == 2
    assert env["correlation_id"] == inbound["correlation_id"] and env["in_reply_to"] == inbound["id"]
    at_limit = ch.reply({**asked, "hops": ch.MAX_HOPS}, {"kind": "update", "summary": "x"}, NOW)
    assert at_limit[0] is None and any("hops" in p for p in at_limit[1])


def test_any_barb_persona_and_dieter_are_valid_recipients() -> None:
    for to in ("barb", "rd-barb", "dieter", "*"):
        assert _compose(to=to)[1] == []


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"to": "someone-else"}, "to must be one of"),
        ({"to": None}, "to must be one of"),
        ({"correlation_id": "thread-7"}, "correlation_id"),
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


def test_a_bad_recipient_is_reported_once() -> None:
    _, problems = _compose(to=None)
    assert sum("to " in p for p in problems) == 1


def test_exchange_issue_round_trips_through_parse() -> None:
    env, _ = _compose()
    assert env is not None
    issue = ch.to_exchange_issue(env)
    assert issue["title"] == "[question] Pick the memory route"
    assert issue["labels"] == ["barb-inbox", "to:barb", "from:claude-planner"]
    assert "For rd-barb import" in issue["body"]
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
    assert ch.send({**QUESTION, "to": "nobody"}, rec, NOW, live=True)["error"] == "invalid_envelope"
    assert len(rec.sent) == 1  # an invalid envelope never reaches the transport


def test_live_switch_reads_only_the_kill_switch() -> None:
    envs: list[dict[str, str]] = [{}, {"CLAUDE_BARB_CHANNEL": "off"}, {"CLAUDE_BARB_CHANNEL": "on"}]
    assert [ch.channel_live(e) for e in envs] == [False, False, True]


def test_exchange_transport_requires_a_repo_and_uses_the_injected_creator() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    env, _ = _compose()
    assert env is not None
    missing = ch.ExchangeTransport("", lambda repo, issue: "x").deliver(env)
    assert missing["error"] == "config_missing"
    transport = ch.ExchangeTransport("owner/exchange", lambda repo, issue: (calls.append((repo, issue)), "url-1")[1])
    assert transport.deliver(env) == {"ok": True, "status": "sent", "ref": "url-1"}
    assert calls[0][0] == "owner/exchange" and calls[0][1]["labels"][0] == "barb-inbox"
