"""Sign-in alerts only for providers the fleet can dispatch to (#1712 follow-up).

Cline is registered with ``dispatch_mode="future"``: nothing dispatches to it, and
it has no synchronous credential probe, so the inbox raised a permanent HIGH
"Sign-in required: Cline" alert the owner could never clear.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from agent_remediation import provider_probe
from agent_remediation.provider_registry import PROVIDER_REGISTRY
from staff import inbox


def _entry(dashboard_id: str):
    return next(e for e in PROVIDER_REGISTRY if e.dashboard_id == dashboard_id)


@pytest.mark.unit
def test_future_provider_raises_no_sign_in_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    cline = replace(_entry("cline"), enabled=True)
    codex = replace(_entry("codex_cli"), enabled=True)
    unauth = {"installed": True, "authenticated": False, "detail": "not signed in"}
    monkeypatch.setattr("agent_remediation.PROVIDER_REGISTRY", (cline, codex))
    monkeypatch.setattr(provider_probe, "probe_provider_availability", lambda: {"cline": unauth, "codex_cli": unauth})

    ids = [i.id for i in inbox._collect_auth_sign_ins()]  # noqa: SLF001

    assert cline.dispatch_mode == "future"
    assert ids == ["auth_codex_cli"]
