"""Fleet Job Board S6 (#2787): briefing wiring and coordination-api doc parity."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from coordination_fake_rm import FakeRM, board, install, session
from fastapi.testclient import TestClient
from routers import coordination as coordination_router

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COORD_DOC = _REPO_ROOT / "docs" / "coordination-api.md"
_DOC_ROUTE = re.compile(r"^### `(GET|POST) (/api/coordination/[^`]+)`", re.MULTILINE)


def _coordination_router_paths() -> set[str]:
    prefix = coordination_router.router.prefix
    out: set[str] = set()
    for route in coordination_router.router.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if not path or not methods.intersection({"GET", "POST", "PUT", "PATCH", "DELETE"}):
            continue
        full = path if path.startswith(prefix) else f"{prefix}{path}"
        out.add(full)
    return out


def _documented_coordination_paths() -> set[str]:
    text = _COORD_DOC.read_text(encoding="utf-8")
    return {path for _, path in _DOC_ROUTE.findall(text)}


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yield from install(tmp_path, monkeypatch, {"bot-token": __import__("coordination_fake_rm", fromlist=["bot"]).bot("agent-codex")}, repos="Tools")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=("100.64.0.9", 51000))


@pytest.mark.unit
def test_briefing_lists_jobs_endpoints_and_hint(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:list", board(session("s1", "Tools")))
    body = client.get("/api/coordination/briefing?repo=Tools&agent=codex").json()
    endpoints = body["endpoints"]
    assert "/api/coordination/jobs" in endpoints["jobs"]
    assert "/api/coordination/jobs/releases" in endpoints["jobs_releases"]
    hint = body["jobs_hint"]
    assert "/api/coordination/jobs" in hint
    assert "POST /api/coordination/claims" in hint


@pytest.mark.unit
def test_documented_coordination_routes_match_router() -> None:
    router_paths = _coordination_router_paths()
    doc_paths = _documented_coordination_paths()
    missing_from_docs = sorted(router_paths - doc_paths)
    extra_in_docs = sorted(doc_paths - router_paths)
    assert not missing_from_docs, f"coordination routes missing from docs/coordination-api.md: {missing_from_docs}"
    assert not extra_in_docs, f"coordination-api.md documents routes not on router: {extra_in_docs}"
