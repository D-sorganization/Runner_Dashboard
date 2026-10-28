"""A Barb principal writing without the lease claim headers is observed, not silently trusted (#3279 step 1).

``DASHBOARD_BARB_FENCE_MODE`` = ``off`` | ``warn`` (default) | ``enforce``. In ``warn`` a Barb-principal
write with no ``X-Barb-Scope`` / ``X-Barb-Lease-Epoch`` is logged and counted
(``dashboard_barb_unclaimed_writes_total``) but still allowed; ``enforce`` refuses it with the fence's
``stale_barb_lease`` 409. Non-Barb requests are never touched.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, identity_manager
from prometheus_client import REGISTRY

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.33", 52033)
SCOPE = "barb:pr-queue"
METRIC = "dashboard_barb_unclaimed_writes_total"
FENCE_LOGGER = "dashboard.barb_fence"
pytestmark = pytest.mark.integration

_MEM = ["memory.read", "memory.write", "memory.shared.summarize"]
_TOKENS = {
    "barb": Principal(id="agent-grok", type="bot", name="Barb", roles=["bot"], scopes=_MEM, assistant_kind="grokbot"),
    "plain": Principal(id="agent-plain", type="bot", name="Plain", roles=["bot"], scopes=_MEM),
}
_MEMORY = {"entry_type": "session_digest", "source_product": "grokbot", "body": {"summary": "x", "open_items": []}}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import orchestration_audit  # noqa: PLC0415
    from memory.store import reset_memory_store  # noqa: PLC0415
    from staff import barb_continuity  # noqa: PLC0415
    from staff.audit import reset_audit_store  # noqa: PLC0415

    monkeypatch.setattr(barb_continuity, "_DEFAULT_STORE", barb_continuity.BarbContinuityStore(tmp_path / "b.sqlite3"))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setenv("STAFF_AUDIT_DB", str(tmp_path / "staff_audit.sqlite3"))
    monkeypatch.delenv("DASHBOARD_BARB_FENCE_MODE", raising=False)
    monkeypatch.setattr(orchestration_audit, "ORCHESTRATION_AUDIT_PATH", tmp_path / "audit.ndjson")
    reset_memory_store()
    reset_audit_store()
    for name, prin in _TOKENS.items():
        identity_manager.add_ephemeral_token(hashlib.sha256(f"tok3279-{name}".encode()).hexdigest(), prin, name=name)
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)
    reset_memory_store()
    reset_audit_store()


def _h(name: str, scope: str | None = None, epoch: int | None = None) -> dict[str, str]:
    h = {**_XHR, "Authorization": f"Bearer tok3279-{name}"}
    if scope is not None:
        h["X-Barb-Scope"] = scope
    if epoch is not None:
        h["X-Barb-Lease-Epoch"] = str(epoch)
    return h


def _count(surface: str = "memory") -> float:
    return REGISTRY.get_sample_value(METRIC, {"surface": surface}) or 0.0


def _fence_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == FENCE_LOGGER and r.levelno == logging.WARNING]


def test_barb_write_without_claim_is_logged_counted_and_allowed_in_warn_mode(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    before = _count()
    with caplog.at_level(logging.WARNING, logger=FENCE_LOGGER):
        r = client.post("/api/v1/memory", headers=_h("barb"), json=_MEMORY)
    assert r.status_code == 201, r.text
    assert _count() == before + 1
    warnings = _fence_warnings(caplog)
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "agent-grok" in msg and "memory" in msg
    assert "tok3279" not in msg  # never echo the bearer


def test_non_barb_write_without_claim_is_not_logged_or_counted(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    before = _count()
    with caplog.at_level(logging.WARNING, logger=FENCE_LOGGER):
        r = client.post("/api/v1/memory", headers=_h("plain"), json=_MEMORY)
    assert r.status_code == 201, r.text
    assert _count() == before
    assert _fence_warnings(caplog) == []


def test_barb_write_with_claim_is_fenced_as_before(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    lease = client.post("/api/v1/staff/barb/leases/checkout", headers=_h("plain"), json={"scope": SCOPE})
    assert lease.status_code == 200, lease.text
    epoch = lease.json()["lease"]["epoch"]
    before = _count()
    with caplog.at_level(logging.WARNING, logger=FENCE_LOGGER):
        r = client.post("/api/v1/memory", headers=_h("barb", SCOPE, epoch), json=_MEMORY)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["reason"] == "holder_mismatch"
    assert _count() == before
    assert not [w for w in _fence_warnings(caplog) if "without" in w.getMessage()]


def test_flag_off_behaves_like_before(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_BARB_FENCE_MODE", "off")
    before = _count()
    with caplog.at_level(logging.WARNING, logger=FENCE_LOGGER):
        r = client.post("/api/v1/memory", headers=_h("barb"), json=_MEMORY)
    assert r.status_code == 201, r.text
    assert _count() == before
    assert _fence_warnings(caplog) == []


def test_enforce_refuses_an_unclaimed_barb_write_with_the_fence_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_BARB_FENCE_MODE", "enforce")
    before = _count()
    r = client.post("/api/v1/memory", headers=_h("barb"), json=_MEMORY)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "stale_barb_lease"
    assert r.json()["detail"]["reason"] == "claim_required"
    assert _count() == before + 1
    assert client.post("/api/v1/memory", headers=_h("plain"), json=_MEMORY).status_code == 201
    # the staff v1 surfaces share the policy (their error envelope carries the 409 status)
    turnover = {"scope": SCOPE, "summary": "shift over"}
    assert client.post("/api/v1/staff/barb/turnover", headers=_h("barb"), json=turnover).status_code == 409
    assert client.post("/api/v1/staff/barb/turnover", headers=_h("plain"), json=turnover).status_code == 200


@pytest.mark.unit
def test_unknown_mode_falls_back_to_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    from staff.barb_lease_fence import fence_mode  # noqa: PLC0415

    monkeypatch.setenv("DASHBOARD_BARB_FENCE_MODE", "ENFORCE ")
    assert fence_mode() == "enforce"
    monkeypatch.setenv("DASHBOARD_BARB_FENCE_MODE", "bogus")
    assert fence_mode() == "warn"
    monkeypatch.delenv("DASHBOARD_BARB_FENCE_MODE")
    assert fence_mode() == "warn"


@pytest.mark.unit
def test_barb_seat_identity_excludes_admins() -> None:
    from memory.service import is_barb_principal  # noqa: PLC0415
    from staff.barb_lease_fence import is_barb_seat  # noqa: PLC0415

    admin = Principal(id="op", type="human", name="Op", roles=["admin"], scopes=[])
    assert is_barb_seat(_TOKENS["barb"]) and not is_barb_seat(_TOKENS["plain"]) and not is_barb_seat(admin)
    assert is_barb_principal(admin)  # memory's barb-only read access still includes admins
