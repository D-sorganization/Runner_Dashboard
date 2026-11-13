"""barb_cli (#3072): async send/ask-barb, caller-only inbox, missing_scope, exactly-once sends."""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock

import pytest
from fleet_validators import FleetAPIError

from scripts import barb_cli

SECRET = "svc_TOPSECRET_never_print_me"  # pragma: allowlist secret
OPTS = [{"label": "Merge", "recommended": True}, {"label": "Hold"}]
REASON = "Required checks are green."


class FakeHub:
    """In-memory staff threads (id = title); ``race`` makes an in-flight duplicate insert 500, like #3024."""

    def __init__(self, agent: str = "rd-barb", scopes: tuple[str, ...] = ("fleet.read",), race: bool = False) -> None:
        self.agent, self.scopes, self.race, self.calls = agent, scopes, race, []  # type: ignore[var-annotated]
        self.msgs: dict[str, list[dict[str, Any]]] = {}
        self._lock, self._inflight = threading.Lock(), set()  # type: ignore[var-annotated]

    def request(self, method: str, path: str, *, params: Any = None, body: Any = None, headers: Any = None) -> Any:
        self.calls.append((method, path))
        if method == "GET" and path in (barb_cli.THREADS, "/api/coordination/sessions"):
            if "fleet.read" not in self.scopes:
                raise FleetAPIError(403, {"detail": {"required_scope": "fleet.read", "auth": f"Bearer {SECRET}"}})
            return {"threads": [{"id": t} for t in self.msgs], "sessions": [{"agent": "codex"}]}
        if path == barb_cli.THREADS:  # create replays per Idempotency-Key (#1795)
            self.msgs.setdefault(body["title"], [])
            return {"id": body["title"]}
        tid = path.split("/")[5]
        if method == "GET":
            return {"messages": self.msgs[tid]}
        key = headers["Idempotency-Key"]
        with self._lock:
            if any(m["idempotency_key"] == key for m in self.msgs[tid]):
                return {"replay": True}
            if self.race and key in self._inflight:
                raise FleetAPIError(500, {"detail": "UNIQUE constraint failed"})
            self._inflight.add(key)
        time.sleep(0.05)
        self.msgs[tid].append({"idempotency_key": key, **body})
        return {}


def _cli(hub: Any, now: float = 1200.0, sleep: Any = lambda _s: None, agent: str = "") -> barb_cli.BarbCli:
    return barb_cli.BarbCli(hub, agent=agent or hub.agent, token=SECRET, clock=lambda: now, sleep=sleep)


def _posted(hub: FakeHub) -> list[dict[str, Any]]:
    return [m["meta"]["rd_threads"] for msgs in hub.msgs.values() for m in msgs]


def test_send_returns_correlation_id_fast_without_waiting_on_a_model() -> None:
    hub, start = FakeHub(), time.monotonic()
    out = _cli(hub).send(to="codex", kind="update", summary="merged #1")
    assert time.monotonic() - start < 2.0 and out["ok"] is True and out["correlation_id"].startswith("c_")
    assert all(m == "POST" for m, _ in hub.calls)  # never reads or waits for a reply
    assert (_posted(hub)[0]["to"], _posted(hub)[0]["from"]) == ("codex", "rd-barb")


def test_ask_barb_question_board_broadcast_and_invalid_rejected_before_io() -> None:
    hub = FakeHub()
    assert _cli(hub).ask_barb(summary="Merge #9?", options=OPTS, recommendation_reason=REASON)["ok"]
    env = _posted(hub)[0]
    assert (env["kind"], env["to"], env["recommended"], env["ack_required"]) == ("question", "barb", "Merge", True)
    _cli(hub).send(to="*", kind="update", summary="queue drained")
    assert barb_cli.BOARD_TITLE in hub.msgs
    calls = len(hub.calls)
    bad = _cli(hub).send(to="dieter", kind="question", summary="?", options=[{"label": "A"}, {"label": "B"}])
    assert bad["error"] == "invalid_envelope" and len(hub.calls) == calls
    no_reason = _cli(hub).ask_barb(summary="Merge #9?", options=OPTS)
    assert no_reason["error"] == "invalid_envelope" and any("recommendation_reason" in p for p in no_reason["problems"])


def test_same_payload_in_window_posts_once_and_returns_same_correlation_id() -> None:
    hub = FakeHub()
    a = _cli(hub).send(to="codex", kind="update", summary="x")
    b = _cli(hub, now=1230.0).send(to="codex", kind="update", summary="x")
    assert a["correlation_id"] == b["correlation_id"] and b["status"] == "replayed" and len(_posted(hub)) == 1


def test_concurrent_duplicate_send_is_exactly_once_3024() -> None:
    hub = FakeHub(race=True)
    _cli(hub).send(to="codex", kind="update", summary="warm")  # thread exists before the race
    send = lambda c: c.send(to="codex", kind="update", summary="dup")  # noqa: E731
    with ThreadPoolExecutor(2) as pool:  # two independent senders, like two processes
        results = list(pool.map(send, [_cli(hub, sleep=time.sleep) for _ in range(2)]))
    assert [r["ok"] for r in results] == [True, True]
    assert [e["summary"] for e in _posted(hub)].count("dup") == 1


def test_inbox_returns_unread_items_for_the_calling_principal_only() -> None:
    hub = FakeHub()
    for to, summary in (("rd-barb", "for barb"), ("claude", "for claude"), ("*", "for all")):
        _cli(hub, agent="codex").send(to=to, kind="update", summary=summary)
    _cli(hub).send(to="*", kind="update", summary="mine")
    out = _cli(hub).inbox()
    assert out["ok"] is True and sorted(i["summary"] for i in out["items"]) == ["for all", "for barb"]


@pytest.mark.parametrize("verb", ["inbox", "peers"])
def test_missing_fleet_read_fails_closed_with_missing_scope(verb: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    out = getattr(_cli(FakeHub(scopes=())), verb)()
    assert out == {"ok": False, "error": "missing_scope", "required_scope": "fleet.read"} and SECRET not in caplog.text


def test_no_token_in_any_output_or_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    leaky = MagicMock(**{"request.side_effect": FleetAPIError(502, {"echo": f"Authorization: Bearer {SECRET}"})})
    out = _cli(leaky, agent="a").send(to="b", kind="update", summary="x")
    assert out["ok"] is False and out["error"] == "http_502" and SECRET not in caplog.text + json.dumps(out)


def test_peers_and_main_parse_verbs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(barb_cli, "build_cli", lambda: _cli(FakeHub()))
    assert barb_cli.main(["send", '{"to": "codex", "kind": "update", "summary": "hi"}']) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert barb_cli.main(["peers"]) == 0 and json.loads(capsys.readouterr().out)["peers"][0]["agent"] == "codex"
    assert barb_cli.main(["inbox", '{"bogus": 1}']) == 1


def test_ask_barb_main_requires_reason_flag(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(barb_cli, "build_cli", lambda: _cli(FakeHub()))
    opts = json.dumps(OPTS)
    assert barb_cli.main(["ask-barb", f'{{"summary": "?", "options": {opts}}}', "--reason", REASON]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert barb_cli.main(["ask-barb", f'{{"summary": "?", "options": {opts}}}']) == 1
