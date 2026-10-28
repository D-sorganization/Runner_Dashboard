"""Adversarial fixtures for the deny-by-default redaction gate (#3095, private-roles slice 2).

Content originating from a private role is withheld on every shared surface
unless explicitly classified shareable (``Level.PUBLIC``). Each ``CASES``
entry plants a classification span through one shared-surface write boundary
and asserts the raw sentinel never reaches read-back -- mirroring the
existing adversarial-fixture pattern in
``tests/unit/test_staff_redaction_everywhere.py``.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import private_mode as pm  # noqa: E402
from private_mode import Level  # noqa: E402
from staff.barb_continuity import BarbContinuityStore  # noqa: E402
from staff.conversations import ConversationStore, get_conversation_store, reset_conversation_store  # noqa: E402
from staff.private_surface_gate import (  # noqa: E402
    MALFORMED_PLACEHOLDER,
    PLACEHOLDER,
    MalformedClassification,
    gate_text,
    tag,
)
from staff.redaction import redact_sensitive_content  # noqa: E402
from staff.runner import StaffRunner  # noqa: E402
from staff.store import RunRecord, RunStore  # noqa: E402
from staff.thread_bus import reset_thread_bus  # noqa: E402

# Built at runtime, like the secret-redaction suite's planted token, so no
# literal private-looking value sits in the repository.
SENTINEL = "SENTINEL-" + "private-slice2-do-not-echo"
SPAN = tag(SENTINEL, Level.SENSITIVE)


def _clean(stored: Any) -> bool:
    text = stored if isinstance(stored, str) else json.dumps(stored)
    return SENTINEL not in text


@pytest.fixture
def conv(tmp_path: Path):
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


@pytest.fixture
def continuity(tmp_path: Path) -> BarbContinuityStore:
    return BarbContinuityStore(tmp_path / "continuity.sqlite3")


def _thread(conv: ConversationStore, **kw: Any) -> str:
    return conv.create_thread(title=kw.get("title", "t"), kind="direct", participants=["barb"], meta=kw.get("meta")).id


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


# Each case writes a SENSITIVE-classified SPAN through one shared-surface
# write boundary and returns what was persisted (board/thread, panel-backed
# thread, turnover ledger, task inventory, run history, run prompt).
CASES: dict[str, Callable[[ConversationStore, RunStore, BarbContinuityStore], Any]] = {
    "board.thread.title": lambda c, r, b: c.get_thread(_thread(c, title=SPAN)).title,  # type: ignore[union-attr]
    "board.message.body": lambda c, r, b: (
        c.add_message(thread_id=_thread(c), author_kind="role", author="panel:expert-1", body_md=SPAN).body_md
    ),
    "board.proposal.params": lambda c, r, b: (
        c.get_proposal(
            c.create_proposal(
                message_id=c.add_message(thread_id=_thread(c), author_kind="role", author="barb", body_md="x").id,
                thread_id=_thread(c),
                action="test.noop",
                params={"note": SPAN},
                risk="low",
            ).id
        ).params
    ),  # type: ignore[union-attr]
    "run.history.prompt": lambda c, r, b: r.get_run(_run(r, prompt=SPAN)).prompt,  # type: ignore[union-attr]
    "run.history.event": lambda c, r, b: (r.append_event(_run(r), "text", SPAN), r.events_after("run_1"))[1],
    "turnover.summary": lambda c, r, b: b.append_turnover(scope="barb:front-door", author="op", summary=SPAN)[
        "summary"
    ],
    "turnover.blockers": lambda c, r, b: b.append_turnover(
        scope="barb:front-door", author="op", summary="ok", blockers=[SPAN]
    )["blockers"],
    "inventory.title": lambda c, r, b: b.upsert_task(title=SPAN)["title"],
    "inventory.next_action": lambda c, r, b: b.upsert_task(title="t", next_action=SPAN)["next_action"],
}


@pytest.mark.unit
@pytest.mark.parametrize("field", sorted(CASES))
def test_classified_content_never_reaches_a_shared_surface(
    field: str, conv: ConversationStore, runs: RunStore, continuity: BarbContinuityStore
) -> None:
    stored = CASES[field](conv, runs, continuity)
    assert stored, f"{field}: nothing was stored"
    assert _clean(stored), f"{field} leaked private content: {stored!r}"
    assert PLACEHOLDER in (stored if isinstance(stored, str) else json.dumps(stored))


@pytest.mark.unit
def test_transcript_surface_withholds_classified_content(tmp_path: Path, runs: RunStore) -> None:
    run_id = _run(runs)
    transcript = tmp_path / "transcript.log"
    adapter = SimpleNamespace(parse_line=lambda line: {"kind": "text", "text": line})
    proc = SimpleNamespace(stdout=iter([SPAN + "\n", "STAFF_RESULT: done\n"]))
    runner = StaffRunner(store=runs)
    runner._pump_output(runs.get_run(run_id), adapter, proc, transcript, None)  # type: ignore[arg-type]  # noqa: SLF001
    written = transcript.read_text(encoding="utf-8")
    assert "STAFF_RESULT: done" in written
    assert _clean(written), written
    assert PLACEHOLDER in written


@pytest.mark.unit
def test_explicitly_public_classification_is_shareable() -> None:
    """Deny-by-default has one escape: an explicit PUBLIC tag, and only that."""
    shareable = redact_sensitive_content(tag(SENTINEL, Level.PUBLIC))
    assert SENTINEL in shareable


@pytest.mark.unit
def test_mixed_public_and_private_content_in_one_payload() -> None:
    payload = f"public preamble {tag('public-part', Level.PUBLIC)} then {SPAN} trailer"
    out = gate_text(payload)
    assert "public-part" in out
    assert SENTINEL not in out
    assert PLACEHOLDER in out
    assert "trailer" in out


@pytest.mark.unit
def test_malformed_level_name_is_withheld_not_passed_through() -> None:
    malformed = "<<PRIVATE:NOT_A_LEVEL>>" + SENTINEL + "<<END_PRIVATE>>"
    out = gate_text(malformed)
    assert SENTINEL not in out
    assert MALFORMED_PLACEHOLDER in out


@pytest.mark.unit
def test_unterminated_tag_is_withheld() -> None:
    unterminated = "<<PRIVATE:SENSITIVE>>" + SENTINEL + " and no closing delimiter"
    out = gate_text(unterminated)
    assert SENTINEL not in out


@pytest.mark.unit
def test_nested_tag_is_rejected_as_malformed() -> None:
    """An attacker nesting a PUBLIC wrapper around a SENSITIVE span must not launder content.

    ``tag()`` itself refuses to build this (nesting is rejected at that boundary
    too); this test constructs the raw text directly, as a crafted/relayed
    payload would, to prove the parser itself also fails closed on nesting.
    """
    nested = f"<<PRIVATE:PUBLIC>><<PRIVATE:SENSITIVE>>{SENTINEL}<<END_PRIVATE>><<END_PRIVATE>>"
    out = gate_text(nested)
    assert SENTINEL not in out


@pytest.mark.unit
def test_quoted_and_base64_encoded_fragment_is_still_withheld() -> None:
    """Encoding doesn't bypass the tag-based gate the way it can bypass a pattern scanner."""
    encoded = base64.b64encode(SENTINEL.encode()).decode()
    quoted = f'"{encoded}"'
    out = gate_text(tag(quoted, Level.SENSITIVE))
    assert encoded not in out
    assert SENTINEL not in out


@pytest.mark.unit
def test_long_output_is_fully_withheld_not_truncated_into_a_leak() -> None:
    long_value = (SENTINEL + " ") * 5000
    out = gate_text(tag(long_value, Level.SENSITIVE))
    assert SENTINEL not in out
    assert out == PLACEHOLDER


@pytest.mark.unit
def test_tag_rejects_non_level_and_non_string_input() -> None:
    with pytest.raises(MalformedClassification):
        tag(SENTINEL, "SENSITIVE")  # type: ignore[arg-type]
    with pytest.raises(MalformedClassification):
        tag(123, Level.PUBLIC)  # type: ignore[arg-type]


@pytest.mark.unit
def test_tag_rejects_values_that_already_contain_gate_delimiters() -> None:
    with pytest.raises(MalformedClassification):
        tag("<<PRIVATE:PUBLIC>>" + SENTINEL, Level.SENSITIVE)


@pytest.mark.unit
def test_canary_a_bypass_that_merely_strips_delimiters_would_leak() -> None:
    """Canary (DbC): proves the suite has teeth.

    A plausible regression is "resolve" a classification span by stripping
    its delimiters instead of checking its level -- that keeps the text
    looking clean but leaks the content. This test shows that bypass *does*
    leak, and that the real ``gate_text`` does not, so a future refactor
    that accidentally reintroduces the naive bypass fails this file.
    """
    naive_bypass = re.sub(r"<<PRIVATE:[A-Za-z_]*>>|<<END_PRIVATE>>", "", SPAN)
    assert SENTINEL in naive_bypass, "the canary bypass must actually leak for this test to prove anything"
    assert SENTINEL not in gate_text(SPAN)


@pytest.mark.unit
def test_canary_wiring_into_redact_sensitive_content_is_not_skipped() -> None:
    """Canary: if the integration point in staff.redaction drops the gate call, this fails."""
    assert redact_sensitive_content(SPAN) == gate_text(SPAN)
    assert SENTINEL not in redact_sensitive_content(SPAN)


def _write_case_with_span(
    field: str, span_expr: str, conv: ConversationStore, runs: RunStore, continuity: BarbContinuityStore
) -> Any:
    writers: dict[str, Callable[[], Any]] = {
        "board.thread.title": lambda: conv.get_thread(_thread(conv, title=span_expr)).title,  # type: ignore[union-attr]
        "board.message.body": lambda: (
            conv.add_message(
                thread_id=_thread(conv), author_kind="role", author="panel:expert-1", body_md=span_expr
            ).body_md
        ),
        "board.proposal.params": lambda: (
            conv.get_proposal(
                conv.create_proposal(
                    message_id=conv.add_message(
                        thread_id=_thread(conv), author_kind="role", author="barb", body_md="x"
                    ).id,
                    thread_id=_thread(conv),
                    action="test.noop",
                    params={"note": span_expr},
                    risk="low",
                ).id
            ).params
        ),  # type: ignore[union-attr]
        "run.history.prompt": lambda: runs.get_run(_run(runs, prompt=span_expr)).prompt,  # type: ignore[union-attr]
        "run.history.event": lambda: (runs.append_event(_run(runs), "text", span_expr), runs.events_after("run_1"))[1],
        "turnover.summary": lambda: continuity.append_turnover(scope="barb:front-door", author="op", summary=span_expr)[
            "summary"
        ],
        "turnover.blockers": lambda: continuity.append_turnover(
            scope="barb:front-door", author="op", summary="ok", blockers=[span_expr]
        )["blockers"],
        "inventory.title": lambda: continuity.upsert_task(title=span_expr)["title"],
        "inventory.next_action": lambda: continuity.upsert_task(title="t", next_action=span_expr)["next_action"],
    }
    return writers[field]()


@pytest.mark.unit
@pytest.mark.parametrize("form", ["raw", "base64", "url_encoded", "split"])
@pytest.mark.parametrize("field", sorted(CASES))
def test_pm5_leak_canary_forms_absent_across_all_gate_sinks(
    field: str, form: str, conv: ConversationStore, runs: RunStore, continuity: BarbContinuityStore
) -> None:
    """PM-5 (#3255): raw, base64, URL-encoded, and split canaries are withheld across every #3095 sink."""
    variants = pm.build_canary_variants(SENTINEL)
    planted = variants.for_form(form)
    span_expr = (
        f"{tag(planted[0], Level.SENSITIVE)} {tag(planted[1], Level.SENSITIVE)}"
        if isinstance(planted, tuple)
        else tag(planted, Level.SENSITIVE)
    )
    stored = _write_case_with_span(field, span_expr, conv, runs, continuity)
    pm.assert_no_canary_leak(stored, SENTINEL, sink_name=field)


@pytest.mark.unit
@pytest.mark.parametrize("form", ["raw", "base64", "url_encoded", "split"])
@pytest.mark.parametrize("surface", sorted(pm.SHARED_SURFACES))
def test_pm5_leak_canary_forms_absent_across_all_emit_shared_sinks(surface: str, form: str) -> None:
    """PM-5 (#3255): raw, base64, URL-encoded, and split canaries never leak from emit_shared()."""
    variants = pm.build_canary_variants(SENTINEL)
    planted = variants.for_form(form)
    payload = (
        {
            "id": f"example-corp/{planted[0]}",
            "repo": f"example-corp/{planted[1]}",
            "status": planted[0],
            "outcome": planted[1],
            "summary": f"Example Corp {planted[0]} {planted[1]}",
        }
        if isinstance(planted, tuple)
        else {
            "id": f"example-corp/{planted}",
            "repo": "example-corp/widgets",
            "status": planted,
            "summary": f"Example Corp {planted}",
        }
    )
    cfg = pm.PrivateModeConfig(enabled=True, default_level=Level.INTERNAL)
    projection = pm.emit_shared(surface, payload, level=Level.SENSITIVE, config=cfg)
    pm.assert_no_canary_leak(projection.to_dict(), SENTINEL, sink_name=surface)


@pytest.mark.unit
@pytest.mark.parametrize("form", ["raw", "base64", "url_encoded", "split"])
def test_pm5_canary_of_the_canary_bypass_fails_gate(form: str) -> None:
    """PM-5 (#3255): a bypass fixture that leaks any canary form makes the gate fail."""
    variants = pm.build_canary_variants(SENTINEL)
    planted = variants.for_form(form)
    leaked_payload = {"a": planted[0], "b": planted[1]} if isinstance(planted, tuple) else {"body": planted}
    assert form in pm.detect_canary_leaks(leaked_payload, SENTINEL)
    with pytest.raises(pm.CanaryLeakError, match=form):
        pm.assert_no_canary_leak(leaked_payload, SENTINEL, sink_name="bypass_fixture")


@pytest.mark.unit
def test_pm5_flag_off_behaviour_unchanged() -> None:
    """PM-5 (#3255): when private_mode is DISABLED, emit_shared preserves PUBLIC behaviour."""
    proj = pm.emit_shared(
        "board",
        {"id": "example-corp/widgets#1", "status": "ok", "summary": "Example Corp public status"},
        level=Level.SENSITIVE,
        config=pm.DISABLED,
    )
    assert proj.level is Level.PUBLIC and proj.withheld is False
    assert proj.content["summary"] == "Example Corp public status"
