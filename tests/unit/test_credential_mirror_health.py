"""Credential mirror health surfaced on staff summary (#3820)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from staff.credential_mirror_health import (  # noqa: E402
    attention_items_from_mirror_health,
    probe_credential_mirror_health,
)


@pytest.mark.unit
def test_attention_item_for_stale_mirror() -> None:
    report = {
        "ok": False,
        "providers": {
            "claude": {
                "ok": False,
                "stale_copies": [{"path": "/tmp/.claude/.credentials.json", "reason": "older_than_source"}],
            }
        },
    }
    items = attention_items_from_mirror_health(report, machine="Desk")
    assert len(items) == 1
    assert items[0]["failure_class"] == "credential_mirror_stale"
    assert items[0]["provider"] == "claude"


@pytest.mark.unit
def test_probe_includes_mirror_audit_in_provider_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import staff.provider_auth_preflight as preflight

    wsl_home = tmp_path / "home"
    wsl_home.mkdir()
    cred = wsl_home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-x", "accessToken": "a-x", "expiresAt": 4000}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(wsl_home))
    monkeypatch.setattr(
        "staff.credential_mirror_health._SYNC_SCRIPT",
        Path(__file__).resolve().parents[2] / "deploy" / "sync_provider_credentials.py",
    )
    # #4393: the audit walks /mnt/c/Users on a WSL host and compared the fixture
    # with the operator's real Windows credential copies. Audit the fake home only.
    import staff.credential_mirror_health as mirror_health

    load_sync_module = mirror_health._load_sync_module

    def load_sync_module_without_windows_homes():  # noqa: ANN202
        sync_mod = load_sync_module()
        sync_mod.discover_windows_homes = lambda *_a, **_k: []
        return sync_mod

    monkeypatch.setattr(mirror_health, "_load_sync_module", load_sync_module_without_windows_homes)

    with patch("staff.provider_auth_preflight.provider_oauth_session_alive", return_value=(True, "ok")):
        report = preflight.probe_local_provider_auth("Desk")
    mirrors = report.get("credential_mirrors") or {}
    assert mirrors.get("ok") is True
    assert "providers" in mirrors
    assert "r-x" not in json.dumps(mirrors)


@pytest.mark.unit
def test_probe_mirror_health_never_raises_when_script_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "staff.credential_mirror_health._SYNC_SCRIPT",
        tmp_path / "missing-sync.py",
    )
    report = probe_credential_mirror_health()
    assert report.get("skipped") is True
    assert report.get("ok") is True
    assert attention_items_from_mirror_health(report, machine="Desk") == []
