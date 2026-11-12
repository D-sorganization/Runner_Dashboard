"""Tests for scripts/fleet_run_jwt.py (#3879 step 3)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

import fleet_run_jwt as mod
from scripts import fleet_run_jwt as cli


def test_fetch_run_jwt_exchanges_bootstrap_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    class _Client:
        def __init__(self, token: str) -> None:
            sent["bootstrap"] = token

        def request(
            self,
            method: str,
            path: str,
            body: Any = None,
            headers: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            sent.update({"method": method, "path": path, "body": body, "headers": headers or {}})
            return {"token": "aaa.bbb.ccc", "expires_at": 999.0, "jti": "jti-abc"}

    import fleet_client

    monkeypatch.setenv("FLEET_API_TOKEN", "bootstrap-static-bearer")
    monkeypatch.setenv("FLEET_RUN_JWT_RUN_ID", "run-77")
    monkeypatch.setenv("FLEET_RUN_JWT_PRINCIPAL_ID", "agent-grok")
    monkeypatch.setenv("FLEET_RUN_JWT_CALLER_NODE", "barb-vm")
    monkeypatch.setattr(fleet_client, "FleetClient", _Client)

    token, expires_at, jti = mod.fetch_run_jwt()

    assert token == "aaa.bbb.ccc"
    assert expires_at == 999.0
    assert jti == "jti-abc"
    assert sent == {
        "bootstrap": "bootstrap-static-bearer",
        "method": "POST",
        "path": "/api/fleet/run-jwt",
        "body": {"run_id": "run-77", "principal_id": "agent-grok"},
        "headers": {"X-Fleet-Node": "barb-vm"},
    }


def test_fetch_run_jwt_redacts_bootstrap_in_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_validators import FleetAPIError

    class _Client:
        def __init__(self, token: str) -> None:
            pass

        def request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise FleetAPIError(403, {"detail": "unregistered fleet node"})

    import fleet_client

    secret = "bootstrap-static-bearer-for-redaction-test"  # pragma: allowlist secret
    monkeypatch.setenv("FLEET_API_TOKEN", secret)
    monkeypatch.setenv("FLEET_RUN_JWT_RUN_ID", "run-1")
    monkeypatch.setenv("FLEET_RUN_JWT_PRINCIPAL_ID", "agent-grok")
    monkeypatch.setenv("FLEET_RUN_JWT_CALLER_NODE", "barb-vm")
    monkeypatch.setattr(fleet_client, "FleetClient", _Client)

    with pytest.raises(RuntimeError, match="run-jwt request failed") as exc:
        mod.fetch_run_jwt()
    assert secret not in str(exc.value)


def test_main_token_prints_jwt_without_logging_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    jwt_value = "hdr.pay.sig"

    def _fake_fetch(**_kwargs: Any) -> tuple[str, float, str]:
        return jwt_value, 1.0, "jti-x"

    monkeypatch.setattr(cli, "fetch_run_jwt", _fake_fetch)
    caplog.set_level(logging.ERROR)
    assert cli.main(["token"]) == 0
    assert capsys.readouterr().out == jwt_value
    assert jwt_value not in caplog.text
