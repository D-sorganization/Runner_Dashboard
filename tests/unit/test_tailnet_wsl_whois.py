"""WSL2 Windows-hosted tailscaled whois bridge (#4577)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import tailnet_identity


def test_wsl_whois_disabled_without_socket_or_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_WSL_WHOIS", "0")
    monkeypatch.setattr(tailnet_identity, "_running_in_wsl2", lambda: True)
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_via_unix", lambda *_: None)
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_via_wsl_cli", lambda *_: tailnet_identity.WhoIsIdentity(login="a@b", name="A"))
    assert tailnet_identity._tailscaled_whois("100.64.0.2", 443) is None


def test_wsl_whois_uses_cli_after_unix_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_WSL_WHOIS", "1")
    monkeypatch.setattr(tailnet_identity, "_running_in_wsl2", lambda: True)
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_via_unix", lambda *_: None)
    expected = tailnet_identity.WhoIsIdentity(login="operator@github", name="Operator")
    cli = Mock(return_value=expected)
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_via_wsl_cli", cli)
    result = tailnet_identity._tailscaled_whois("100.64.0.2", 8443)
    assert result == expected
    cli.assert_called_once_with("100.64.0.2", 8443)


def test_wsl_cli_whois_parses_tailscale_exe_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"UserProfile": {"LoginName": "alice@tail", "DisplayName": "Alice"}}
    completed = SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode())
    monkeypatch.setattr(tailnet_identity, "_wsl_tailscale_exe", lambda: "/mnt/c/Program Files/Tailscale/tailscale.exe")
    run = Mock(return_value=completed)
    monkeypatch.setattr(tailnet_identity.subprocess, "run", run)
    result = tailnet_identity._tailscaled_whois_via_wsl_cli("100.64.0.9", 5555)
    assert result == tailnet_identity.WhoIsIdentity(login="alice@tail", name="Alice")
    argv = run.call_args.args[0]
    assert "--json" in argv


def test_wsl_cli_whois_fails_closed_on_mismatch_remains_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI errors or bad JSON never admit a principal — caller must match login."""
    monkeypatch.setattr(tailnet_identity, "_wsl_tailscale_exe", lambda: "/mnt/c/Program Files/Tailscale/tailscale.exe")
    monkeypatch.setattr(
        tailnet_identity.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(returncode=1, stdout=b"")),
    )
    assert tailnet_identity._tailscaled_whois_via_wsl_cli("100.64.0.9", 5555) is None


def test_unix_whois_wins_before_wsl_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_WSL_WHOIS", "1")
    unix = tailnet_identity.WhoIsIdentity(login="unix@login", name="Unix")
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_via_unix", lambda *_: unix)
    cli = Mock(return_value=tailnet_identity.WhoIsIdentity(login="cli@login", name="Cli"))
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_via_wsl_cli", cli)
    assert tailnet_identity._tailscaled_whois("127.0.0.1", 12345) == unix
    cli.assert_not_called()