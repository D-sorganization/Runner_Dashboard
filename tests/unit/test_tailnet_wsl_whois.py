"""WSL2 Windows-hosted tailscale whois bridge (#4577)."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import tailnet_identity


def _payload(login: str, display: str = "") -> str:
    profile: dict[str, str] = {"LoginName": login}
    if display:
        profile["DisplayName"] = display
    return json.dumps({"UserProfile": profile})


def test_wsl_whois_disabled_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-22.04")
    monkeypatch.delenv("DASHBOARD_TAILSCALE_WSL_WHOIS", raising=False)
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_localapi", lambda host, port: None)
    run = Mock()
    monkeypatch.setattr(tailnet_identity.subprocess, "run", run)
    assert tailnet_identity._tailscaled_whois("127.0.0.1", 8321) is None
    run.assert_not_called()


def test_wsl_whois_missing_cli_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-22.04")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_WSL_WHOIS", "1")
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_localapi", lambda host, port: None)
    monkeypatch.setattr(tailnet_identity.os.path, "isfile", lambda path: False)
    assert tailnet_identity._tailscaled_whois("127.0.0.1", 8321) is None


def test_wsl_whois_parses_matching_windows_cli_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-22.04")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_WSL_WHOIS", "1")
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_localapi", lambda host, port: None)
    monkeypatch.setattr(tailnet_identity.os.path, "isfile", lambda path: True)

    completed = Mock(returncode=0, stdout=_payload("operator@github.com", "Operator"))
    run = Mock(return_value=completed)
    monkeypatch.setattr(tailnet_identity.subprocess, "run", run)

    result = tailnet_identity._tailscaled_whois("127.0.0.1", 54321)
    assert result == tailnet_identity.WhoIsIdentity(login="operator@github.com", name="Operator")
    argv = run.call_args.args[0]
    assert "--json" in argv


def test_wsl_whois_malformed_payload_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-22.04")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_WSL_WHOIS", "1")
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_localapi", lambda host, port: None)
    monkeypatch.setattr(tailnet_identity.os.path, "isfile", lambda path: True)

    completed = Mock(returncode=0, stdout='{"UserProfile": {}}')
    monkeypatch.setattr(tailnet_identity.subprocess, "run", Mock(return_value=completed))
    assert tailnet_identity._tailscaled_whois("127.0.0.1", 54321) is None


def test_localapi_success_skips_windows_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = tailnet_identity.WhoIsIdentity(login="owner@example.com", name="Owner")
    monkeypatch.setattr(tailnet_identity, "_tailscaled_whois_localapi", lambda host, port: identity)
    run = Mock()
    monkeypatch.setattr(tailnet_identity.subprocess, "run", run)
    assert tailnet_identity._tailscaled_whois("127.0.0.1", 8321) is identity
    run.assert_not_called()
