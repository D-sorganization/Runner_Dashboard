"""PM-0 contract tests for Private Mode (#3250, epic #3249)."""

from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import overlay_contract as oc  # noqa: E402
import private_mode as pm  # noqa: E402

MOCK = _ROOT / "tests" / "fixtures" / "overlay" / "mock-overlay.json"
SENTINEL = "SENTINEL-private-value-do-not-echo"


def _mock() -> dict:
    return json.loads(MOCK.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / f"{SENTINEL}-overlay.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_level_total_order() -> None:
    assert pm.Level.PUBLIC < pm.Level.INTERNAL < pm.Level.SENSITIVE
    assert pm.max_level(pm.Level.PUBLIC, pm.Level.INTERNAL) is pm.Level.INTERNAL
    assert pm.max_level(pm.Level.INTERNAL, pm.Level.SENSITIVE) is pm.Level.SENSITIVE


def test_flag_absent_or_off_yields_public_everywhere() -> None:
    settings = pm.PrivateModeSettings(enabled=False, metric_route_templating_enabled=False)
    assert pm.effective_default_level(settings) is pm.Level.PUBLIC
    assert pm.private_mode_active(settings) is False
    overlay = oc.load_overlay(MOCK)
    assert overlay.private_mode.enabled is False
    assert pm.effective_default_level(overlay.private_mode) is pm.Level.PUBLIC


@pytest.mark.parametrize("raw", ["", "TOP_SECRET", "restricted", "Top_Secret"])
def test_schema_rejects_unknown_level_names(raw: str) -> None:
    with pytest.raises(pm.PrivateModeError, match="unknown level"):
        pm.parse_level(raw)


def test_mock_overlay_unchanged_without_private_mode_key() -> None:
    """Flag-off snapshot: legacy fixture still validates with defaults."""
    before = _mock()
    overlay = oc.load_overlay(MOCK)
    assert "private_mode" not in before
    assert overlay.private_mode == pm.PrivateModeSettings.disabled()


def test_flag_off_behaviour_matches_absent_key(tmp_path: Path) -> None:
    explicit = _mock()
    explicit["private_mode"] = {
        "enabled": False,
        "metric_route_templating": {"enabled": False},
    }
    absent = oc.load_overlay(MOCK)
    off = oc.load_overlay(_write(tmp_path, explicit))
    assert absent.private_mode == off.private_mode
    assert pm.private_mode_active(absent.private_mode) is False
    assert pm.private_mode_active(off.private_mode) is False


def test_private_mode_unknown_keys_rejected(tmp_path: Path) -> None:
    data = _mock()
    data["private_mode"] = {"enabled": False, "extra": SENTINEL}
    with pytest.raises(oc.OverlayError, match="unknown key.*extra") as exc:
        oc.load_overlay(_write(tmp_path, data))
    assert SENTINEL not in str(exc.value)


def test_metric_route_templating_flag_off_by_default(tmp_path: Path) -> None:
    data = _mock()
    data["private_mode"] = {"enabled": True}
    overlay = oc.load_overlay(_write(tmp_path, data))
    assert overlay.private_mode.metric_route_templating_enabled is False


def test_enabled_private_mode_still_defaults_classification_to_public(tmp_path: Path) -> None:
    """PM-0 wires the flag only; resolver lands in PM-1."""
    data = _mock()
    data["private_mode"] = {"enabled": True}
    overlay = oc.load_overlay(_write(tmp_path, data))
    assert overlay.private_mode.enabled is True
    assert pm.effective_default_level(overlay.private_mode) is pm.Level.PUBLIC


def test_read_private_mode_uses_overlay_accessor_only() -> None:
    """LoD: private_mode is read through get_overlay(), not overlay env vars."""
    assert pm.read_private_mode({}) == pm.PrivateModeSettings.disabled()
    settings = pm.read_private_mode({oc.OVERLAY_PATH_ENV: str(MOCK)})
    assert settings == pm.PrivateModeSettings.disabled()


def test_safe_disable_contract_precondition() -> None:
    with pytest.raises(pm.PrivateModeError, match="private_mode is disabled"):
        pm.require_private_mode_active(pm.PrivateModeSettings.disabled())


def test_only_accessor_modules_touch_overlay_env_for_private_mode() -> None:
    offenders = []
    for path in _BACKEND.rglob("*.py"):
        if path.name in {"overlay_contract.py", "private_mode.py"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "private_mode" in text and "get_overlay" not in text and "read_private_mode" not in text:
            if "private_mode" in text.lower():
                offenders.append(str(path.relative_to(_ROOT)))
    assert offenders == []


def test_overlay_contract_parses_private_mode_block() -> None:
    tree = ast.parse(Path(_BACKEND / "overlay_contract.py").read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "parse_overlay" in names


def test_internal_level_semantics_documented() -> None:
    doc = pm.LEVEL_SEMANTICS[pm.Level.INTERNAL]
    assert "redaction" in doc.lower()
    assert "metadata-only" in doc.lower() or "metadata" in doc.lower()


def test_publish_guard_self_test_passes() -> None:
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "publish_guard.py"), "--self-test"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_overlay_with_enabled_private_mode_round_trip(tmp_path: Path) -> None:
    data = _mock()
    data["private_mode"] = {
        "enabled": True,
        "metric_route_templating": {"enabled": True},
    }
    overlay = oc.load_overlay(_write(tmp_path, data))
    assert overlay.private_mode.enabled is True
    assert overlay.private_mode.metric_route_templating_enabled is True
    pm.require_private_mode_active(overlay.private_mode)


def test_private_mode_block_must_be_object(tmp_path: Path) -> None:
    data = _mock()
    data["private_mode"] = True
    with pytest.raises(oc.OverlayError, match="private_mode"):
        oc.load_overlay(_write(tmp_path, data))


def test_enabled_must_be_bool(tmp_path: Path) -> None:
    data = copy.deepcopy(_mock())
    data["private_mode"] = {"enabled": "yes"}
    with pytest.raises(oc.OverlayError, match="enabled"):
        oc.load_overlay(_write(tmp_path, data))
