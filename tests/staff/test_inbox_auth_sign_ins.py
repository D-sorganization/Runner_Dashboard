"""Sign-in alerts only for providers the fleet can dispatch to (#1712 follow-up).

Cline is registered with ``dispatch_mode="future"``: nothing dispatches to it, and
it has no synchronous credential probe, so the inbox raised a permanent HIGH
"Sign-in required: Cline" alert the owner could never clear.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from agent_remediation import provider_probe
from agent_remediation.provider_registry import PROVIDER_REGISTRY
from staff import inbox
from staff.inbox import InboxItem, collect_inbox


def _entry(dashboard_id: str):
    return next(e for e in PROVIDER_REGISTRY if e.dashboard_id == dashboard_id)


@pytest.mark.unit
def test_future_provider_raises_no_sign_in_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    cline = replace(_entry("cline"), enabled=True)
    codex = replace(_entry("codex_cli"), enabled=True)
    unauth = {"installed": True, "authenticated": False, "detail": "not signed in"}
    monkeypatch.setattr("agent_remediation.PROVIDER_REGISTRY", (cline, codex))
    monkeypatch.setattr(provider_probe, "probe_provider_availability", lambda: {"cline": unauth, "codex_cli": unauth})

    items = inbox._collect_auth_sign_ins()  # noqa: SLF001
    ids = [i.id for i in items]

    assert cline.dispatch_mode == "future"
    assert ids == ["auth_sign_in"]
    assert len(items) == 1
    assert "1 provider needs sign-in" in items[0].title
    assert "Codex CLI" in items[0].summary
    assert "/settings#credentials" in items[0].link or "/settings" in items[0].link
    assert items[0].details == [{"provider": "codex_cli", "label": "Codex CLI", "reason": "not signed in"}]


@pytest.mark.unit
def test_claude_login_file_satisfies_auth_without_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """ANTHROPIC_API_KEY unset but Claude login file present means no sign-in item."""
    claude = replace(_entry("claude_code_cli"), enabled=True)
    monkeypatch.setattr("agent_remediation.PROVIDER_REGISTRY", (claude,))
    # Simulate probe reporting authenticated: True (via login file)
    auth_via_login = {"installed": True, "authenticated": True, "detail": "Ready (claude login)"}
    monkeypatch.setattr(provider_probe, "probe_provider_availability", lambda: {"claude_code_cli": auth_via_login})

    items = inbox._collect_auth_sign_ins()  # noqa: SLF001
    assert items == []


@pytest.mark.unit
def test_multiple_providers_aggregated_into_single_auth_item(monkeypatch: pytest.MonkeyPatch) -> None:
    codex = replace(_entry("codex_cli"), enabled=True)
    claude = replace(_entry("claude_code_cli"), enabled=True)
    unauth_codex = {"installed": True, "authenticated": False, "detail": "codex not signed in"}
    unauth_claude = {"installed": True, "authenticated": False, "detail": "claude not signed in"}
    monkeypatch.setattr("agent_remediation.PROVIDER_REGISTRY", (codex, claude))
    monkeypatch.setattr(
        provider_probe,
        "probe_provider_availability",
        lambda: {"codex_cli": unauth_codex, "claude_code_cli": unauth_claude},
    )

    items = inbox._collect_auth_sign_ins()  # noqa: SLF001
    assert len(items) == 1
    item = items[0]
    assert item.id == "auth_sign_in"
    assert item.title == "2 providers need sign-in"
    assert "Codex CLI" in item.summary
    assert "Claude Code CLI" in item.summary
    assert len(item.details) == 2
    assert any(d["provider"] == "codex_cli" for d in item.details)
    assert any(d["provider"] == "claude_code_cli" for d in item.details)


@pytest.mark.unit
def test_auth_sign_in_severity_medium_when_some_provider_available(monkeypatch: pytest.MonkeyPatch) -> None:
    codex = replace(_entry("codex_cli"), enabled=True)
    claude = replace(_entry("claude_code_cli"), enabled=True)
    unauth_codex = {"installed": True, "authenticated": False, "detail": "codex not signed in"}
    auth_claude = {"installed": True, "authenticated": True, "detail": "Ready (claude login)"}
    monkeypatch.setattr("agent_remediation.PROVIDER_REGISTRY", (codex, claude))
    monkeypatch.setattr(
        provider_probe,
        "probe_provider_availability",
        lambda: {"codex_cli": unauth_codex, "claude_code_cli": auth_claude},
    )

    items = inbox._collect_auth_sign_ins()  # noqa: SLF001
    assert len(items) == 1
    assert items[0].severity == "medium"


@pytest.mark.unit
def test_auth_sign_in_severity_high_when_all_active_providers_signed_out(monkeypatch: pytest.MonkeyPatch) -> None:
    codex = replace(_entry("codex_cli"), enabled=True)
    claude = replace(_entry("claude_code_cli"), enabled=True)
    unauth_codex = {"installed": True, "authenticated": False, "detail": "codex not signed in"}
    unauth_claude = {"installed": True, "authenticated": False, "detail": "claude not signed in"}
    monkeypatch.setattr("agent_remediation.PROVIDER_REGISTRY", (codex, claude))
    monkeypatch.setattr(
        provider_probe,
        "probe_provider_availability",
        lambda: {"codex_cli": unauth_codex, "claude_code_cli": unauth_claude},
    )

    items = inbox._collect_auth_sign_ins()  # noqa: SLF001
    assert len(items) == 1
    assert items[0].severity == "high"


@pytest.mark.unit
def test_auth_sign_ins_count_is_providers_not_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """The single aggregated item still reports how many providers need sign-in."""
    aggregated = InboxItem(
        id="auth_sign_in",
        source="auth_sign_in",
        title="3 providers need sign-in",
        summary="",
        severity="medium",
        created_at="2026-09-27T10:00:00Z",
        link="/settings#credentials",
        details=[{"provider": p, "label": p, "reason": ""} for p in ("codex", "claude", "gemini")],
    )
    monkeypatch.setattr(inbox, "github_inbox_sources_enabled", lambda: False)
    monkeypatch.setattr(inbox, "_collect_auth_sign_ins", lambda: [aggregated])
    monkeypatch.setattr(inbox, "_collect_approvals", lambda s: [])
    monkeypatch.setattr(inbox, "_collect_needs_input", lambda *a: [])
    monkeypatch.setattr(inbox, "_collect_escalations", lambda s: [])

    aggregate = asyncio.run(collect_inbox(c_store=None, r_store=None, w_store=None))

    assert [it.id for it in aggregate.items] == ["auth_sign_in"]
    assert aggregate.counts["auth_sign_ins"] == 3
