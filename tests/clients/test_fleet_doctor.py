"""Read-only ``fleetctl doctor`` checks (#1802 step 1): pure checks, mocked HTTP, no network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import fleetctl
import pytest
from fleet_client import (
    CLIENT_ENVELOPE_VERSION,
    REQUIRED_SCOPES,
    CheckResult,
    FleetClient,
    check_briefing_freshness,
    check_dns_tls,
    check_roundtrip,
    check_scopes,
    check_token,
    check_version,
    run_doctor,
)
from fleet_fixtures import _clean_fleet_env, fake_api  # pytest fixtures

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


def _ok_version() -> dict[str, Any]:
    return {"dashboard": "1.0", "envelope": {"min": 1, "max": 1, "supported": ["dispatch-envelope.v1"]}}


# ------------------------------------------------------------------ DNS / TLS


def test_dns_failure_has_remedy() -> None:
    def resolver(host: str, port: int) -> Any:
        raise OSError("name not known")

    result = check_dns_tls("https://nohost.invalid:8321", resolver=resolver, tls_probe=lambda h, p: None)
    assert not result.ok
    assert "FLEET_API_URL" in result.remedy
    assert "nohost.invalid" in result.detail


def test_tls_failure_distinct_from_dns() -> None:
    def probe(host: str, port: int) -> None:
        raise OSError("certificate verify failed")

    result = check_dns_tls("https://h.example:443", resolver=lambda h, p: [("x",)], tls_probe=probe)
    assert not result.ok
    assert "TLS" in result.detail
    assert "certificate" in result.remedy.lower()


def test_http_skips_tls_probe() -> None:
    def probe(host: str, port: int) -> None:
        raise AssertionError("must not probe TLS for http")

    result = check_dns_tls("http://127.0.0.1:8321", resolver=lambda h, p: [("x",)], tls_probe=probe)
    assert result.ok


# ------------------------------------------------------------------ token / scopes


def test_token_missing() -> None:
    client = FleetClient("http://127.0.0.1:1")
    result, principal = check_token(client)
    assert not result.ok and principal is None
    assert "FLEET_API_TOKEN" in result.remedy


def test_token_revoked_is_401(fake_api: Any) -> None:
    fake_api.respond("GET", "/api/auth/me", 401, {"detail": "invalid token"})
    result, principal = check_token(FleetClient(fake_api.url, token="svc_x"))
    assert not result.ok and principal is None
    assert "revoked" in result.detail.lower()
    assert "mint" in result.remedy.lower()


def test_token_valid_returns_principal(fake_api: Any) -> None:
    fake_api.respond("GET", "/api/auth/me", 200, {"id": "agent-claude", "scopes": ["staff.read"]})
    result, principal = check_token(FleetClient(fake_api.url, token="svc_x"))
    assert result.ok
    assert principal == {"id": "agent-claude", "scopes": ["staff.read"]}
    assert fake_api.last.headers["authorization"] == "Bearer svc_x"


def test_scopes_missing_listed() -> None:
    result = check_scopes({"id": "a", "scopes": ["staff.read"]})
    assert not result.ok
    for scope in REQUIRED_SCOPES:
        if scope != "staff.read":
            assert scope in result.detail
    assert "principals.yml" in result.remedy


def test_scopes_all_present() -> None:
    assert check_scopes({"id": "a", "scopes": list(REQUIRED_SCOPES)}).ok


def test_scopes_without_principal_is_failure() -> None:
    assert not check_scopes(None).ok


# ------------------------------------------------------------------ version


def test_version_compatible() -> None:
    assert check_version(_ok_version()).ok


def test_version_incompatible_is_distinct_from_token_failure() -> None:
    payload = {"envelope": {"min": 2, "max": 3, "supported": ["dispatch-envelope.v2"]}}
    result = check_version(payload)
    assert not result.ok
    assert "incompatible" in result.detail.lower()
    assert str(CLIENT_ENVELOPE_VERSION) in result.detail
    assert "upgrade" in result.remedy.lower()
    token_fail, _ = check_token(FleetClient("http://127.0.0.1:1"))
    assert result.detail != token_fail.detail and result.remedy != token_fail.remedy


def test_version_malformed_payload() -> None:
    result = check_version({"nonsense": True})
    assert not result.ok
    assert "envelope" in result.detail


# ------------------------------------------------------------------ freshness


def test_briefing_fresh() -> None:
    stamp = (NOW - timedelta(seconds=30)).isoformat()
    assert check_briefing_freshness({"generated_at": stamp}, now=NOW).ok


def test_briefing_stale() -> None:
    stamp = (NOW - timedelta(hours=2)).isoformat()
    result = check_briefing_freshness({"generated_at": stamp}, now=NOW, max_age_seconds=300)
    assert not result.ok
    assert "stale" in result.detail.lower()


def test_briefing_missing_timestamp() -> None:
    assert not check_briefing_freshness({}, now=NOW).ok
    assert not check_briefing_freshness({"generated_at": "garbage"}, now=NOW).ok


# ------------------------------------------------------------------ roundtrip + runner


def test_roundtrip_opens_thread(fake_api: Any) -> None:
    fake_api.respond("POST", "/api/v1/staff/threads", 200, {"id": "t1"})
    result = check_roundtrip(FleetClient(fake_api.url, token="svc_x"))
    assert result.ok
    opened = next(r for r in fake_api.requests if r.path == "/api/v1/staff/threads")
    assert opened.body["role"] == "barb"


def _wire(fake_api: Any, *, generated_at: str) -> None:
    fake_api.respond("GET", "/api/auth/me", 200, {"id": "a", "scopes": list(REQUIRED_SCOPES)})
    fake_api.respond("GET", "/api/version", 200, _ok_version())
    fake_api.respond("GET", "/api/coordination/briefing", 200, {"generated_at": generated_at})


def test_run_doctor_all_pass_and_read_only(fake_api: Any) -> None:
    _wire(fake_api, generated_at=NOW.isoformat())
    results = run_doctor(FleetClient(fake_api.url, token="svc_x"), now=NOW)
    assert all(isinstance(r, CheckResult) and r.ok for r in results), results
    assert {r.name for r in results} >= {"reachability", "token", "scopes", "version", "briefing"}
    assert all(r.method == "GET" for r in fake_api.requests)
    assert all(r.name != "roundtrip" for r in results)


def test_run_doctor_roundtrip_only_when_requested(fake_api: Any) -> None:
    _wire(fake_api, generated_at=NOW.isoformat())
    fake_api.respond("POST", "/api/v1/staff/threads", 200, {"id": "t1"})
    results = run_doctor(FleetClient(fake_api.url, token="svc_x"), now=NOW, with_roundtrip=True)
    assert any(r.name == "roundtrip" and r.ok for r in results)


def test_run_doctor_unreachable_short_circuits() -> None:
    results = run_doctor(FleetClient("http://127.0.0.1:1", token="svc_x"), now=NOW)
    assert not all(r.ok for r in results)
    assert any(r.name == "reachability" and not r.ok for r in results)


# ------------------------------------------------------------------ CLI wiring


def test_cli_doctor_exit_0(capsys: pytest.CaptureFixture[str], fake_api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(fake_api, generated_at=datetime.now(UTC).isoformat())
    monkeypatch.setenv("FLEET_API_TOKEN", "svc_x")
    code = fleetctl.main(["--url", fake_api.url, "doctor"])
    out = capsys.readouterr().out
    assert code == 0
    assert "PASS" in out and "FAIL" not in out


def test_cli_doctor_exit_1_on_revoked_token(
    capsys: pytest.CaptureFixture[str], fake_api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(fake_api, generated_at=datetime.now(UTC).isoformat())
    fake_api.respond("GET", "/api/auth/me", 401, {"detail": "revoked"})
    monkeypatch.setenv("FLEET_API_TOKEN", "svc_x")
    code = fleetctl.main(["--url", fake_api.url, "doctor"])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in out and "remedy" in out.lower()


def test_cli_doctor_json(capsys: pytest.CaptureFixture[str], fake_api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(fake_api, generated_at=datetime.now(UTC).isoformat())
    monkeypatch.setenv("FLEET_API_TOKEN", "svc_x")
    code = fleetctl.main(["--url", fake_api.url, "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["ok"] is True and data["checks"]


def test_cli_doctor_no_roundtrip_by_default(
    capsys: pytest.CaptureFixture[str], fake_api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(fake_api, generated_at=datetime.now(UTC).isoformat())
    monkeypatch.setenv("FLEET_API_TOKEN", "svc_x")
    fleetctl.main(["--url", fake_api.url, "doctor"])
    assert all(r.method == "GET" for r in fake_api.requests)
