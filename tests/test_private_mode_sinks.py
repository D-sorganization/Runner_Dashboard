"""Contract and PM-5 canary tests for Private Mode PM-6 sink adapters (issue #3256, epic #3249)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
for _p in (str(_BACKEND), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import private_mode as pm  # noqa: E402
import private_mode_emit as pme  # noqa: E402
import private_mode_sinks as pms  # noqa: E402
import publish_guard as pg  # noqa: E402
from activity import ActivityEnvelope  # noqa: E402

CANARY = "CANARY-" + "example-corp-pm6-sink-secret"
_TS = "2026-10-28T22:00:00Z"
_REPO = "example-corp/widgets-sensitive"
_CFG = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)


def _sample_activity_envelope(secret: str) -> ActivityEnvelope:
    pl = {"id": f"ec/{secret}", "status": "running", "outcome": "pending", "summary": secret}
    base = {
        "schema_version": 1,
        "event_id": "evt-example-corp-1",
        "source": "coordination",
        "principal": "agent-example",
        "node": "node-example-a",
        "session_id": "sess-example-1",
        "seq": 7,
        "ts": _TS,
        "repo": _REPO,
        "issue": 42,
        "branch": f"feat/{secret}",
        "pr": 99,
        "kind": "status",
        "redaction_version": 1,
        "payload": pl,
    }
    return ActivityEnvelope.model_validate(base)


def _invoke_sink_adapter(
    sink_name: str, secret: str, *, level: pm.Level, config: pm.PrivateModeConfig, delivered: list[dict[str, Any]]
) -> pms.SinkEmission:
    c: dict[str, Any] = {"ts": _TS, "level": level, "config": config, "sink": delivered.append}
    if sink_name == "board":
        return pms.emit_board_entry(entry_id=f"b-{secret}", repo=_REPO, author="op", status="running", body=secret, **c)
    if sink_name == "turnover":
        return pms.emit_turnover_entry(
            entry_id=f"t-{secret}", scope="s", author="op", status="ok", summary=secret, blockers=(secret,), **c
        )
    if sink_name == "inventory":
        return pms.emit_inventory_item(item_id=f"i-{secret}", repo=_REPO, status="parked", title=secret, **c)
    if sink_name == "panels":
        return pms.emit_panel_entry(
            thread_id=f"p-{secret}", seat="argon", status="ok", outcome="succeeded", body_md=secret, **c
        )
    if sink_name == "run_history":
        return pms.emit_run_history_entry(
            run_id=f"r-{secret}", role_id="b", repo=_REPO, status="ok", outcome="ok", prompt=secret, **c
        )
    if sink_name == "hub_logs":
        flds = {"d": secret, "repo": _REPO}
        return pms.emit_hub_log_record(
            record_id=f"l-{secret}", logger_name="hub", severity="info", status="ok", message=secret, fields=flds, **c
        )
    if sink_name == "activity":
        return pms.emit_activity_envelope(_sample_activity_envelope(secret), **c)
    if sink_name == "github_check":
        return pms.emit_github_check_run(
            run_id=f"c-{secret}",
            repo=_REPO,
            name="G",
            status="completed",
            conclusion="failure",
            title=secret,
            summary=secret,
            text=secret,
            **c,
        )
    return pms.emit_alert_event(
        alert_id=f"a-{secret}",
        repo=_REPO,
        target_id="oc",
        kind="warning",
        status="error",
        outcome="parked",
        message=secret,
        **c,
    )


@pytest.mark.parametrize("form", ["raw", "base64", "url_encoded", "split"])
@pytest.mark.parametrize("sink_name", sorted(pms.PM6_SINKS))
def test_pm5_canary_green_across_every_pm6_sink_adapter(sink_name: str, form: str) -> None:
    """Acceptance criterion 1: PM-5 canary green on each newly wired sink across all 4 encodings."""
    planted = pm.build_canary_variants(CANARY).for_form(form)
    secret_text = f"{planted[0]}-{planted[1]}" if isinstance(planted, tuple) else planted
    delivered: list[dict[str, Any]] = []
    em = _invoke_sink_adapter(sink_name, secret_text, level=pm.Level.SENSITIVE, config=_CFG, delivered=delivered)
    assert (em.surface, em.projection.level, em.projection.withheld) == (sink_name, pm.Level.SENSITIVE, True)
    for target in (em.formatted, em.projection.to_dict(), delivered[0]):
        pm.assert_no_canary_leak(target, CANARY, sink_name=sink_name)


def test_github_check_run_output_summary_is_metadata_only_at_sensitive() -> None:
    """Acceptance criterion 2: check-run output.summary is metadata-only at SENSITIVE."""
    delivered: list[dict[str, Any]] = []
    em = pms.emit_github_check_run(
        run_id=f"{_REPO}#88",
        repo=_REPO,
        name="private-ci/gate",
        status="completed",
        conclusion="success",
        title=f"Title {CANARY}",
        summary=f"Summary {CANARY} for {_REPO}",
        text=f"Trace {CANARY}",
        ts=_TS,
        level=pm.Level.SENSITIVE,
        config=_CFG,
        sink=delivered.append,
    )
    chk, output = em.formatted, em.formatted["output"]
    assert (chk["name"], chk["status"], chk["conclusion"]) == ("private-ci/gate", "completed", "success")
    assert str(chk["external_id"]).startswith("opaque:")
    assert set(output) == {"title", "summary"} and output["title"] == "Private Mode check (completed)"
    for tok in (
        "level=SENSITIVE",
        "withheld=true",
        f"opaque_id={em.projection.opaque_id}",
        "status=completed",
        "outcome=succeeded",
    ):
        assert tok in output["summary"]
    assert "example-corp" not in output["summary"] and delivered == [chk]
    pm.assert_no_canary_leak(chk, CANARY, sink_name="github_check")


def test_activity_envelope_sink_adapter_preserves_2858_contract_and_withholds_at_sensitive() -> None:
    """#2858 activity ingestion adapter returns a valid ActivityEnvelope with metadata-only payload at SENSITIVE."""
    delivered: list[dict[str, Any]] = []
    em = pms.emit_activity_envelope(
        _sample_activity_envelope(CANARY), level=pm.Level.SENSITIVE, config=_CFG, sink=delivered.append
    )
    v = ActivityEnvelope.model_validate(em.formatted)
    assert (v.branch, v.issue, v.pr) == (None, None, None) and v.repo is not None and v.repo.startswith("opaque:")
    assert (v.payload["withheld"], v.payload["level"], v.payload["opaque_id"]) == (
        True,
        "SENSITIVE",
        em.projection.opaque_id,
    )
    pm.assert_no_canary_leak(v.model_dump(mode="json"), CANARY, sink_name="activity")
    assert len(delivered) == 1


@pytest.mark.parametrize("sink_name", sorted(pms.PM6_SINKS))
def test_flag_off_behaviour_unchanged_and_redacts_secrets(sink_name: str) -> None:
    """Acceptance criterion 3 & #3545 F03: SENSITIVE stays SENSITIVE when flag off; PUBLIC redacts secrets."""
    fake_token = "ghp_" + ("D" * 36)
    sens_delivered: list[dict[str, Any]] = []
    sens_em = _invoke_sink_adapter(
        sink_name,
        f"Example Corp secret {CANARY} {fake_token}",
        level=pm.Level.SENSITIVE,
        config=pm.DISABLED,
        delivered=sens_delivered,
    )
    assert sens_em.projection.level is pm.Level.SENSITIVE and sens_em.projection.withheld is True
    pm.assert_no_canary_leak(sens_em.formatted, CANARY, sink_name=sink_name)

    delivered: list[dict[str, Any]] = []
    em = _invoke_sink_adapter(
        sink_name,
        f"Example Corp public note {fake_token}",
        level=pm.Level.PUBLIC,
        config=pm.DISABLED,
        delivered=delivered,
    )
    assert em.projection.level is pm.Level.PUBLIC and em.projection.withheld is False and len(delivered) == 1
    assert fake_token not in str(em.formatted) and "Example Corp public note" in str(em.formatted)


def test_adapters_delegate_to_emit_shared_and_pass_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """LoD / DRY / DbC / #3080 publish guard: adapters route through emit_shared and pass all guards."""
    assert pme.find_shared_sink_import_violations(_BACKEND) == []
    seen: list[str] = []
    real_emit = pms.emit_shared

    def _spy(s: str, p: Any, **kw: Any) -> pm.SharedProjection:
        seen.append(s)
        return real_emit(s, p, **kw)

    monkeypatch.setattr(pms, "emit_shared", _spy)
    for sink_name in sorted(pms.PM6_SINKS):
        _invoke_sink_adapter(sink_name, "Example Corp trace", level=pm.Level.INTERNAL, config=_CFG, delivered=[])
    assert sorted(seen) == sorted(pms.PM6_SINKS)

    with pytest.raises(pm.ClassificationError, match="sink_name"):
        pms.emit_sink("unknown_sink", {"id": "1"}, level=pm.Level.PUBLIC, config=_CFG)
    with pytest.raises(pm.ClassificationError, match="entry_id"):
        pms.emit_board_entry(
            entry_id="  ", repo=_REPO, author="op", status="ok", body="hi", level=pm.Level.PUBLIC, config=_CFG
        )
    with pytest.raises(pm.ClassificationError, match="status"):
        pms.emit_github_check_run(
            run_id="c-1", repo=_REPO, name="g", status="bad", conclusion=None, title="t", summary="s", config=_CFG
        )
    with pytest.raises(pm.ClassificationError, match="envelope"):
        pms.emit_activity_envelope("not-an-envelope", level=pm.Level.PUBLIC, config=_CFG)  # type: ignore[arg-type]
    with pytest.raises(pm.ClassificationError, match="unsupported option"):
        pms.emit_board_entry(entry_id="b-1", repo=_REPO, author="op", status="ok", body="hi", config=_CFG, bogus_opt=1)
    assert pg.check(_ROOT, ["backend/private_mode_sinks.py", "tests/test_private_mode_sinks.py"], pg.load_rules()) == []
