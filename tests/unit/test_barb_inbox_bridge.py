"""Barbara barb-inbox bridge (#3236): import/export exchange issues via hub threads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from coordination.barb_inbox_bridge import (
    BarbInboxBridge,
    ExchangeRateLimitError,
)
from coordination.relay_guard import RelayGuard

from scripts import claude_barb_channel as ch
from scripts import rd_threads_envelope as envelope
from tests.api.test_hub_thread_port import FakeThreadHub

NOW = 1_800_000_000.0
QUESTION = {
    "kind": "question",
    "to": "barb",
    "summary": "Pick the memory route",
    "options": [{"label": "GitHub exchange repo", "recommended": True}, {"label": "Reopen ADR 0007"}],
    "recommendation_reason": "Keeps ADR 0007 intact.",
}


class FakeExchangeClient:
    """In-memory GitHub exchange client for bridge tests."""

    def __init__(
        self,
        inbox: list[Mapping[str, Any]] | None = None,
        *,
        rate_limit_after: int | None = None,
        always_rate_limit: bool = False,
    ) -> None:
        self.inbox = list(inbox or [])
        self.created: list[dict[str, Any]] = []
        self._create_calls = 0
        self._rate_limit_after = rate_limit_after
        self._rate_limit_tripped = False
        self._always_rate_limit = always_rate_limit

    def list_inbox_issues(self) -> list[Mapping[str, Any]]:
        return self.inbox

    def create_issue(self, repo: str, issue: Mapping[str, Any]) -> str:
        self._create_calls += 1
        if self._always_rate_limit:
            raise ExchangeRateLimitError("rate limited")
        if (
            self._rate_limit_after is not None
            and not self._rate_limit_tripped
            and self._create_calls > self._rate_limit_after
        ):
            self._rate_limit_tripped = True
            raise ExchangeRateLimitError("rate limited")
        payload = dict(issue)
        self.created.append(payload)
        return f"https://github.com/{repo}/issues/{len(self.created)}"


def _sealed(**over: Any) -> dict[str, Any]:
    env, problems = ch.compose({**QUESTION, **over}, NOW)
    assert problems == [] and env is not None
    return env


def _outbound(**over: Any) -> dict[str, Any]:
    msg = {
        "kind": "answer",
        "from": "rd-barb",
        "to": ch.SENDER,
        "summary": over.pop("summary", "reply"),
        "body": over.pop("body", "ok"),
        "hops": 1,
        **over,
    }
    return envelope.seal(msg, NOW)


def _bridge(
    client: FakeExchangeClient | None = None,
    *,
    hub: FakeThreadHub | None = None,
    buffer_size: int = 4,
    live: bool = False,
) -> BarbInboxBridge:
    hub = hub or FakeThreadHub()
    guard = RelayGuard(frozenset({"barb", "rd-barb", "claude-planner"}))
    env = {"CLAUDE_BARB_CHANNEL": "on" if live else "off"}
    return BarbInboxBridge.build(
        hub,
        guard,
        client or FakeExchangeClient(),
        repo="owner/exchange",
        env=env,
        buffer_size=buffer_size,
    )


def test_import_writes_one_thread_message_per_issue_and_is_idempotent() -> None:
    sealed = _sealed()
    issue = ch.to_exchange_issue(sealed)
    client = FakeExchangeClient([{"number": 11, "body": issue["body"]}])
    bridge = _bridge(client, live=True)

    first = bridge.import_inbox()
    again = bridge.import_inbox()

    assert first.imported == 1 and first.refused == 0
    assert again.imported == 0 and again.skipped == 1
    assert len(bridge.hub.writes) == 1


def test_export_renders_a_valid_exchange_issue() -> None:
    bridge = _bridge(live=True)
    result = bridge.export_envelope(_outbound())
    assert result.ok and result.status == "sent"
    assert bridge.client.created[-1]["labels"][0] == "barb-inbox"
    parsed, problems = ch.parse_exchange_body(bridge.client.created[-1]["body"])
    assert problems == [] and parsed is not None


def test_secret_bearing_exchange_issue_is_refused_and_not_imported() -> None:
    clean = _sealed()
    leaky = {**clean, "body": "token ghp_" + "a" * 36}  # pragma: allowlist secret
    issue = ch.to_exchange_issue(leaky)
    bridge = _bridge(FakeExchangeClient([{"number": 2, "body": issue["body"]}]), live=True)
    report = bridge.import_inbox()
    assert report.imported == 0 and report.refused == 1
    assert bridge.hub.writes == []


def test_rate_limit_buffers_then_drains_in_order() -> None:
    client = FakeExchangeClient(rate_limit_after=1)
    bridge = _bridge(client, live=True, buffer_size=8)
    assert bridge.export_envelope(_outbound(summary="m0")).status == "sent"
    assert bridge.export_envelope(_outbound(summary="m1")).status == "buffered"
    drained = bridge.flush_outbound()
    assert drained.sent == 1 and drained.remaining == 0
    assert [c["title"] for c in client.created] == ["[answer] m0", "[answer] m1"]


def test_buffer_overflow_dead_letters_without_blocking_local_import() -> None:
    hub = FakeThreadHub()
    sealed = _sealed()
    client = FakeExchangeClient(
        [{"number": 5, "body": ch.to_exchange_issue(sealed)["body"]}],
        always_rate_limit=True,
    )
    bridge = _bridge(client, hub=hub, live=True, buffer_size=1)
    assert bridge.export_envelope(_outbound(summary="one")).status == "buffered"
    assert bridge.export_envelope(_outbound(summary="two")).status == "dead_letter"
    assert len(bridge.dead_letters()) == 1
    report = bridge.import_inbox()
    assert report.imported == 1 and len(hub.writes) == 1


def test_bridge_is_off_by_default_behind_the_channel_flag() -> None:
    bridge = _bridge(live=False)
    assert bridge.import_inbox().status == "dry_run"
    assert bridge.export_envelope(_outbound()).status == "dry_run"


def test_export_never_addresses_dieter_and_routes_duet_traffic_to_rd_barb() -> None:
    bridge = _bridge(live=True)
    payload = envelope.seal(
        {
            "kind": "question",
            "from": "rd-barb",
            "to": "dieter",
            "summary": "reach Dieter",
            "body": "please relay",
            "hops": 1,
        },
        NOW,
    )
    result = bridge.export_envelope(payload)
    assert result.ok and result.routed_to == "rd-barb"
    assert bridge.client.created[-1]["labels"] == [
        "barb-inbox",
        "to:rd-barb",
        "from:rd-barb",
    ]


def test_barb_lease_request_envelope_imports_like_any_other_message() -> None:
    sealed = _sealed(kind="update", summary="lease checkout")
    sealed["barb_lease"] = {"action": "checkout", "scope": "desk", "holder": "cloud"}
    client = FakeExchangeClient([{"number": 9, "body": ch.to_exchange_issue(sealed)["body"]}])
    bridge = _bridge(client, live=True)
    assert bridge.import_inbox().imported == 1
    assert bridge.hub.writes[0][1].get("barb_lease") == sealed["barb_lease"]
