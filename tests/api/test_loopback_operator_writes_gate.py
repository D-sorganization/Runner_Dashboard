"""Issue #2068: loopback must not authorize hold/directive writes without dev opt-in."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import identity
import pytest
from fastapi.testclient import TestClient
from identity import Principal

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_LOOPBACK = ("127.0.0.1", 51000)
_INVALID_BEARER = {"Authorization": "Bearer not-a-real-token"}


@pytest.fixture
def holds_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(tmp_path / "holds.json"))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    monkeypatch.delenv("DASHBOARD_LOOPBACK_OPERATOR_WRITES", raising=False)
    from server import app  # noqa: PLC0415

    with TestClient(app, raise_server_exceptions=False, client=_LOOPBACK) as client:
        yield client


@pytest.fixture
def directives_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    monkeypatch.delenv("DASHBOARD_LOOPBACK_OPERATOR_WRITES", raising=False)
    from server import app  # noqa: PLC0415

    with TestClient(app, raise_server_exceptions=False, client=_LOOPBACK) as client:
        yield client


@pytest.mark.unit
def test_loopback_hold_put_blocked_without_operator_writes_flag(holds_client: TestClient) -> None:
    res = holds_client.put(
        "/api/staff/holds",
        json={"holds": [{"text": "temporary"}]},
        headers=_XHR,
    )
    assert res.status_code == 403
    assert "Loopback operator writes" in str(res.json()["detail"])


@pytest.mark.unit
def test_loopback_hold_put_allowed_with_operator_writes_flag(
    holds_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_OPERATOR_WRITES", "1")
    res = holds_client.put(
        "/api/staff/holds",
        json={"holds": [{"text": "temporary"}]},
        headers=_XHR,
    )
    assert res.status_code == 200


@pytest.mark.unit
def test_invalid_bearer_does_not_elevate_to_loopback_hold_write(holds_client: TestClient) -> None:
    res = holds_client.put(
        "/api/staff/holds",
        json={"holds": [{"text": "temporary"}]},
        headers={**_XHR, **_INVALID_BEARER},
    )
    assert res.status_code == 401


@pytest.mark.unit
def test_loopback_directive_put_blocked_without_flag(directives_client: TestClient) -> None:
    res = directives_client.put(
        "/api/priorities/directives",
        json={"directives": [{"text": "focus"}]},
        headers=_XHR,
    )
    assert res.status_code == 403


@pytest.mark.unit
def test_operator_token_may_write_directives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    monkeypatch.delenv("DASHBOARD_LOOPBACK_OPERATOR_WRITES", raising=False)
    monkeypatch.setattr(
        identity.identity_manager,
        "verify_token",
        lambda raw: Principal(id="op-1", type="human", name="Op", roles=["operator"]) if raw == "op-tok" else None,
    )
    from server import app  # noqa: PLC0415

    with TestClient(app, raise_server_exceptions=False, client=_LOOPBACK) as client:
        res = client.put(
            "/api/priorities/directives",
            json={"directives": [{"text": "authenticated"}]},
            headers={**_XHR, "Authorization": "Bearer op-tok"},
        )
    assert res.status_code == 200
