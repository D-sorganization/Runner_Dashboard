"""Secrets are redacted at every place conversations and runs persist (SC-B1-G6, #1489).

A planted GitHub token and a private IPv4 address are written through each persistence
boundary; nothing read back from the store (or the transcript file) may contain either.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from staff.conversations import ConversationStore, get_conversation_store, reset_conversation_store
from staff.redaction import redact_value
from staff.runner import StaffRunner
from staff.store import RunRecord, RunStore
from staff.thread_bus import reset_thread_bus

# Built at runtime so the secret scanners never see a literal token in the repository.
TOKEN = "ghp_" + "Z" * 36
PRIVATE_IP = "10.1.2.3"
PLANTED = f"use {TOKEN} on {PRIVATE_IP}"


def _clean(stored: Any) -> bool:
    text = stored if isinstance(stored, str) else json.dumps(stored)
    return TOKEN not in text and PRIVATE_IP not in text


@pytest.fixture
def conv(tmp_path: Path) -> Iterator[ConversationStore]:
    reset_conversation_store()
    reset_thread_bus()
    store = get_conversation_store(tmp_path / "conv.sqlite3")
    yield store
    store.close()
    reset_conversation_store()
    reset_thread_bus()


@pytest.fixture
def runs(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "runs.sqlite3")


def _thread(conv: ConversationStore, **kw: Any) -> str:
    return conv.create_thread(title=kw.get("title", "t"), kind="direct", participants=["barb"], meta=kw.get("meta")).id


def _proposal(conv: ConversationStore, params: dict[str, Any]) -> str:
    th = _thread(conv)
    msg = conv.add_message(thread_id=th, author_kind="role", author="barb", body_md="x")
    return conv.create_proposal(message_id=msg.id, thread_id=th, action="test.noop", params=params, risk="low").id


def _run(runs: RunStore, **kw: Any) -> str:
    base: dict[str, Any] = {
        "id": "run_1",
        "role": "ad-hoc",
        "provider": "claude",
        "model": None,
        "machine": "local",
        "repo": "",
        "target_kind": "prompt",
        "target_ref": "",
        "prompt": "p",
    }
    runs.create_run(RunRecord(**{**base, **kw}))
    return "run_1"


# Each case writes PLANTED through one boundary and returns what the store now holds.
CASES: dict[str, Callable[[ConversationStore, RunStore], Any]] = {
    "thread.title (create)": lambda c, r: c.get_thread(_thread(c, title=PLANTED)).title,  # type: ignore[union-attr]
    "thread.title (update)": lambda c, r: c.update_thread(_thread(c), title=PLANTED).title,  # type: ignore[union-attr]
    "thread.meta (create)": lambda c, r: c.get_thread(_thread(c, meta={"note": [PLANTED]})).meta,  # type: ignore[union-attr]
    "thread.meta (update)": lambda c, r: c.update_thread(_thread(c), meta={"n": {"deep": PLANTED}}).meta,  # type: ignore[union-attr]
    "message.meta (add)": lambda c, r: (
        c.add_message(thread_id=_thread(c), author_kind="user", author="u", body_md="b", meta={"cmd": PLANTED}).meta
    ),
    "message.meta (update)": lambda c, r: (
        c.update_message(
            c.add_message(thread_id=_thread(c), author_kind="user", author="u", body_md="b").id, meta={"cmd": PLANTED}
        ).meta
    ),  # type: ignore[union-attr]
    "proposal.params": lambda c, r: c.get_proposal(_proposal(c, {"prompt": PLANTED, "n": 3})).params,  # type: ignore[union-attr]
    "proposal.reason (decide)": lambda c, r: (
        c.decide_proposal(_proposal(c, {}), "denied", decided_by="op", reason=PLANTED).reason
    ),
    "proposal.reason (transition)": lambda c, r: (
        c.transition_proposal_state(_proposal(c, {}), "failed", reason=PLANTED).reason
    ),  # type: ignore[union-attr]
    "run.prompt (create)": lambda c, r: r.get_run(_run(r, prompt=PLANTED)).prompt,  # type: ignore[union-attr]
    "run.error (update)": lambda c, r: (r.update_run(_run(r), error=PLANTED), r.get_run("run_1").error)[1],  # type: ignore[union-attr]
    "run.event text": lambda c, r: (r.append_event(_run(r), "text", PLANTED), r.events_after("run_1"))[1],
    "run.last_line": lambda c, r: (r.append_event(_run(r), "text", PLANTED), r.get_run("run_1").last_line)[1],  # type: ignore[union-attr]
}


@pytest.mark.unit
@pytest.mark.parametrize("field", sorted(CASES))
def test_planted_secrets_are_redacted_on_write(field: str, conv: ConversationStore, runs: RunStore) -> None:
    stored = CASES[field](conv, runs)
    assert stored, f"{field}: nothing was stored"
    assert _clean(stored), f"{field} persisted a secret: {stored!r}"


@pytest.mark.unit
def test_transcript_file_is_redacted(tmp_path: Path, runs: RunStore) -> None:
    run_id = _run(runs)
    transcript = tmp_path / "transcript.log"
    adapter = SimpleNamespace(parse_line=lambda line: {"kind": "text", "text": line})
    proc = SimpleNamespace(stdout=iter([PLANTED + "\n", "STAFF_RESULT: done\n"]))
    runner = StaffRunner(store=runs)
    runner._pump_output(runs.get_run(run_id), adapter, proc, transcript, None)  # type: ignore[arg-type]  # noqa: SLF001
    written = transcript.read_text(encoding="utf-8")
    assert "STAFF_RESULT: done" in written
    assert _clean(written), written


@pytest.mark.unit
def test_redact_value_walks_nested_structures_and_keeps_shape() -> None:
    value = {"a": [PLANTED, {"b": PLANTED}], "n": 3, "ok": True, "none": None, "t": (PLANTED,)}
    out = redact_value(value)
    assert _clean(out)
    assert out["n"] == 3 and out["ok"] is True and out["none"] is None
    assert isinstance(out["a"], list) and isinstance(out["t"], tuple)
    assert out["a"][1]["b"] == "use [REDACTED_GITHUB_TOKEN] on [REDACTED_IP]"
