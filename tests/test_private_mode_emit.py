"""Contract tests for Private Mode PM-4 ``emit_shared()`` choke point (issue #3254, epic #3249)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import private_mode as pm  # noqa: E402
import private_mode_emit as pme  # noqa: E402

SENTINEL = "SENTINEL-" + "example-corp-sensitive-payload"


def _enabled_config(default: pm.Level = pm.Level.INTERNAL) -> pm.PrivateModeConfig:
    return pm.PrivateModeConfig(enabled=True, default_level=default)


def test_sensitive_payload_returns_projection_only_and_never_runs_regex(monkeypatch: pytest.MonkeyPatch) -> None:
    """SENSITIVE payload -> projection only, before any regex in staff.redaction runs."""
    called_redaction = False

    def _boom_redact(_value: Any) -> Any:
        nonlocal called_redaction
        called_redaction = True
        raise AssertionError("staff.redaction must not run on SENSITIVE payloads")

    monkeypatch.setattr(pme, "_redact_for_non_sensitive", _boom_redact)
    delivered: list[dict[str, Any]] = []

    fake_token = "ghp_" + ("C" * 36)
    projection = pm.emit_shared(
        "board",
        {
            "id": "example-corp/widgets-sensitive#42",
            "repo": "example-corp/widgets-sensitive",
            "status": "parked",
            "attempt": 2,
            "ok": False,
            "title": f"Fix {SENTINEL} in Example Corp",
            "body": f"Secret token {fake_token} and {SENTINEL}",
            "unlisted_field": SENTINEL,
        },
        level=pm.Level.SENSITIVE,
        config=_enabled_config(),
        sink=delivered.append,
    )

    assert called_redaction is False
    assert (projection.withheld, projection.level, projection.surface) == (True, pm.Level.SENSITIVE, "board")
    assert projection.content is None and projection.content_mac is None
    assert projection.opaque_id.startswith("opaque:") and "example-corp" not in projection.opaque_id
    assert SENTINEL not in str(projection.to_dict()) and "ghp_" not in str(projection.to_dict())
    assert (projection.metadata["status"], projection.metadata["attempt"], projection.metadata["ok"]) == (
        "parked",
        2,
        False,
    )
    assert str(projection.metadata["id"]).startswith("opaque:")
    assert str(projection.metadata["repo"]).startswith("opaque:")
    assert {"title", "body", "unlisted_field"}.isdisjoint(projection.metadata)
    assert delivered == [projection.to_dict()]


def test_content_hash_omitted_by_default_and_keyed_mac_optional() -> None:
    """No raw sha256 content hashes; omit by default, keyed HMAC optional."""
    cfg = _enabled_config()
    payload = {"id": "item-1", "status": "running", "summary": SENTINEL}

    default_proj = pm.emit_shared("turnover", payload, level=pm.Level.SENSITIVE, config=cfg)
    assert default_proj.content_mac is None and "content_mac" not in default_proj.to_dict()

    with pytest.raises(pm.ClassificationError, match="mac_key"):
        pm.emit_shared("turnover", payload, level=pm.Level.SENSITIVE, config=cfg, include_content_mac=True)

    keyed_proj = pm.emit_shared(
        "turnover",
        payload,
        level=pm.Level.SENSITIVE,
        config=cfg,
        include_content_mac=True,
        mac_key=b"example-corp-test-hmac-key-32bytes!",
    )
    assert keyed_proj.content_mac is not None and keyed_proj.content_mac.startswith("hmac-sha256:")
    assert SENTINEL not in keyed_proj.content_mac


@pytest.mark.parametrize("level", [pm.Level.PUBLIC, pm.Level.INTERNAL])
def test_redaction_runs_on_public_and_internal_payloads(level: pm.Level) -> None:
    """PUBLIC and INTERNAL payloads run through staff.redaction (DRY)."""
    fake_token = "ghp_" + ("A" * 36)
    payload = {"id": "example-corp/widgets#7", "status": "completed", "summary": f"Example Corp {fake_token}"}
    projection = pm.emit_shared("run_history", payload, level=level, config=_enabled_config())
    assert projection.withheld is False and projection.level is level
    assert fake_token not in str(projection.content)
    assert "[REDACTED_GITHUB_TOKEN]" in projection.content["summary"]


def test_flag_off_behaviour_unchanged() -> None:
    """F03 (#3545): when private_mode is DISABLED, SENSITIVE stays SENSITIVE while PUBLIC gets standard redaction."""
    fake_token = "ghp_" + ("B" * 36)
    delivered: list[Any] = []
    payload = {"id": "example-corp/widgets#9", "summary": f"Hello Example Corp {fake_token} {SENTINEL}"}

    sens_proj = pm.emit_shared("board", payload, level=pm.Level.SENSITIVE, config=pm.DISABLED, sink=delivered.append)
    assert sens_proj.level is pm.Level.SENSITIVE and sens_proj.withheld is True
    assert sens_proj.content is None and SENTINEL not in str(sens_proj.to_dict())
    assert delivered == [sens_proj.to_dict()]

    pub_delivered: list[Any] = []
    pub_payload = {"id": "example-corp/widgets#9", "summary": f"Hello Example Corp {fake_token}"}
    projection = pm.emit_shared(
        "board", pub_payload, level=pm.Level.PUBLIC, config=pm.DISABLED, sink=pub_delivered.append
    )
    assert projection.level is pm.Level.PUBLIC and projection.withheld is False
    assert projection.content == {
        "id": "example-corp/widgets#9",
        "summary": "Hello Example Corp [REDACTED_GITHUB_TOKEN]",
    }
    assert pub_delivered == [projection.content]


def test_writer_context_taint_elevates_shared_emission_to_sensitive() -> None:
    """An active SENSITIVE ClassificationEnvelope in contextvars elevates an INTERNAL emission to SENSITIVE."""
    cfg = _enabled_config()
    with pm.envelope_context(pm.stamp_envelope(pm.Level.SENSITIVE, hop="run", config=cfg)):
        projection = pm.emit_shared(
            "hub_logs", {"id": "log-1", "status": "info", "message": SENTINEL}, level=pm.Level.INTERNAL, config=cfg
        )
    assert projection.level is pm.Level.SENSITIVE and projection.withheld is True
    assert projection.content is None and SENTINEL not in str(projection.to_dict())


def test_unknown_surface_or_missing_level_fails_closed() -> None:
    cfg = _enabled_config()
    with pytest.raises(pm.ClassificationError, match="surface"):
        pm.emit_shared("unknown_surface", {"status": "ok"}, level=pm.Level.PUBLIC, config=cfg)
    with pytest.raises(pm.ClassificationError, match="level"):
        pm.emit_shared("board", {"status": "ok"}, level=None, config=cfg)


def test_import_ban_passes_on_backend_and_fails_on_direct_sink_write(tmp_path: Path) -> None:
    """The import-ban test passes on backend/ and fails when any unauthorized module writes a shared sink directly."""
    assert pme.find_shared_sink_import_violations(_BACKEND) == []

    (tmp_path / "rogue_writer.py").write_text(
        "from staff.redaction import redact_sensitive_content\n"
        "def write_board_directly(msg: str) -> str:\n    return redact_sensitive_content(msg)\n",
        encoding="utf-8",
    )
    violations = pme.find_shared_sink_import_violations(tmp_path)
    assert len(violations) == 1 and "rogue_writer.py" in violations[0]

    (tmp_path / "rogue_gate.py").write_text(
        "from staff.private_surface_gate import gate_text\ndef bypass(msg: str) -> str:\n    return gate_text(msg)\n",
        encoding="utf-8",
    )
    assert len(pme.find_shared_sink_import_violations(tmp_path)) == 2


CANARY_PM5 = "CANARY-" + "example-corp-pm5-do-not-leak"


@pytest.mark.parametrize("form", ["raw", "base64", "url_encoded", "split"])
@pytest.mark.parametrize("surface", sorted(pm.SHARED_SURFACES))
def test_pm5_canary_absent_from_every_emit_shared_sink(surface: str, form: str) -> None:
    """PM-5 (#3255): raw, base64, URL-encoded, and split canaries never leak from any shared sink."""
    variants = pm.build_canary_variants(CANARY_PM5)
    planted = variants.for_form(form)
    if isinstance(planted, tuple):
        part_a, part_b = planted
        payload: dict[str, Any] = {
            "id": f"example-corp/{part_a}",
            "repo": f"example-corp/{part_b}",
            "status": part_a,
            "outcome": part_b,
            "title": f"Example Corp {part_a}",
            "summary": f"Example Corp {part_b}",
        }
    else:
        payload = {
            "id": f"example-corp/{planted}",
            "repo": "example-corp/widgets",
            "status": planted,
            "outcome": "completed",
            "title": f"Example Corp {planted}",
            "summary": planted,
        }

    delivered: list[dict[str, Any]] = []
    projection = pm.emit_shared(
        surface,
        payload,
        level=pm.Level.SENSITIVE,
        config=_enabled_config(),
        sink=delivered.append,
    )
    assert pm.detect_canary_leaks(projection.to_dict(), CANARY_PM5) == ()
    pm.assert_no_canary_leak(projection.to_dict(), CANARY_PM5, sink_name=surface)
    assert len(delivered) == 1
    pm.assert_no_canary_leak(delivered[0], CANARY_PM5, sink_name=surface)


@pytest.mark.parametrize("form", ["raw", "base64", "url_encoded", "split"])
def test_pm5_bypass_fixture_makes_canary_gate_fail(form: str) -> None:
    """Canary-of-the-canary (#3255): a bypass sink that leaks any form fails the gate."""
    variants = pm.build_canary_variants(CANARY_PM5)
    planted = variants.for_form(form)
    bypass_output = (
        {"first": planted[0], "second": planted[1]}
        if isinstance(planted, tuple)
        else {"leaked": f"Example Corp {planted}"}
    )
    leaks = pm.detect_canary_leaks(bypass_output, CANARY_PM5)
    assert form in leaks
    with pytest.raises(pm.CanaryLeakError, match=form):
        pm.assert_no_canary_leak(bypass_output, CANARY_PM5, sink_name="bypass_fixture")


def test_pm5_canary_dbc_and_flag_off_contract() -> None:
    """DbC preconditions on canary helpers and flag-off preservation (#3255)."""
    with pytest.raises(pm.ClassificationError, match="canary"):
        pm.build_canary_variants("short")
    with pytest.raises(pm.ClassificationError, match="canary"):
        pm.build_canary_variants(123)  # type: ignore[arg-type]
    variants = pm.build_canary_variants(CANARY_PM5)
    with pytest.raises(pm.ClassificationError, match="form"):
        variants.for_form("rot13")

    for surface in sorted(pm.SHARED_SURFACES):
        sens_proj = pm.emit_shared(
            surface,
            {"id": "example-corp/widgets#1", "status": "ok", "summary": f"Example Corp {CANARY_PM5}"},
            level=pm.Level.SENSITIVE,
            config=pm.DISABLED,
        )
        assert sens_proj.level is pm.Level.SENSITIVE and sens_proj.withheld is True
        pm.assert_no_canary_leak(sens_proj.to_dict(), CANARY_PM5, sink_name=surface)

        proj = pm.emit_shared(
            surface,
            {"id": "example-corp/widgets#1", "status": "ok", "summary": "Example Corp public note"},
            level=pm.Level.PUBLIC,
            config=pm.DISABLED,
        )
        assert proj.level is pm.Level.PUBLIC and proj.withheld is False
        assert proj.content["summary"] == "Example Corp public note"


def test_private_mode_getattr_only_imports_emit_for_exported_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probing unknown attributes on private_mode must not import private_mode_emit (avoids circular import)."""
    monkeypatch.delitem(sys.modules, "private_mode_emit", raising=False)
    with pytest.raises(AttributeError):
        _ = pm.__nonexistent_probe_attr__
    assert "private_mode_emit" not in sys.modules
    assert pm.CANARY_FORMS == ("raw", "base64", "url_encoded", "split")
    assert "private_mode_emit" in sys.modules
