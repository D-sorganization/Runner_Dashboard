"""rd-threads shared envelope (#3072): one schema for rd-barb, barb_cli and the MCP server."""

from typing import Any

import pytest

from scripts import rd_threads_envelope as env

OPTS = [{"label": "Re-arm", "recommended": True}, {"label": "Wait"}]
BASE = {"kind": "question", "from": "rd-barb", "to": "dieter", "summary": "Re-arm?", "options": OPTS}


def _q(**over: Any) -> dict[str, Any]:
    return env.with_defaults({**BASE, **over})


def test_defaults_fill_importance_ack_task_state_and_recommended() -> None:
    q, u = _q(), env.with_defaults({"kind": "update", "from": "a", "to": "*", "summary": "merged"})
    assert (q["importance"], q["urgency"]) == ("normal", "normal")
    assert (q["ack_required"], q["task_state"]) == (True, "input-required")
    assert (u["ack_required"], u["task_state"], u["importance"]) == (False, "completed", "normal")
    assert q["recommended"] == "Re-arm" and env.validate(q) == [] == env.validate(u)


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"options": [{"label": "A"}, {"label": "B"}]}, "exactly one"),
        ({"options": [{"label": "A", "recommended": True}]}, "at least 2"),
        ({"recommended": "Wait"}, "recommended must equal"),
        ({"task_state": "working"}, "input-required"),
        ({"kind": "ping"}, "kind"),
        ({"importance": "max"}, "importance"),
        ({"summary": "two\nlines"}, "summary"),
    ],
)
def test_question_contract_violations_are_named(over: dict[str, Any], needle: str) -> None:
    assert any(needle in p for p in env.validate(_q(**over)))


def test_alert_requires_recommended_action_and_non_object_is_rejected() -> None:
    alert = {"kind": "alert", "from": "rd-barb", "to": "barb", "summary": "Burn guard", "recommended_action": "Pause."}
    assert env.validate(env.with_defaults(alert)) == []
    assert any("recommended_action" in p for p in env.validate(env.with_defaults({**alert, "recommended_action": ""})))
    assert env.validate(["x"]) == ["envelope must be a JSON object"]


def test_dedupe_key_60s_bucket_ignores_generated_ids_and_seals_stable_ids() -> None:
    k0 = env.dedupe_key(_q(), now=1200.0)
    assert len(k0) == 64 and k0 == env.dedupe_key(_q(id="m_x", correlation_id="c_x"), now=1259.9)
    assert env.dedupe_key(_q(), now=1260.0) != k0 and env.dedupe_key(_q(summary="other"), now=1200.0) != k0
    sealed = env.seal(_q(), now=1200.0)
    assert sealed["correlation_id"] == f"c_{env.dedupe_key(sealed, now=1200.0)[:24]}"
    assert env.seal(_q(), now=1210.0)["correlation_id"] == sealed["correlation_id"]
