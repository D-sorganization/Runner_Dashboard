"""Tests for the shared provider registry endpoint (issue #810, epic #809).

GET /api/providers/registry is the ONE source-of-truth contract that both the
dashboard UI and the Conductor orchestrator consume. These tests are the TDD
specification: contract shape, id-mapping correctness (underscore<->hyphen),
injectable + resilient Ollama live-models fetch, the login_status literal
invariant (DbC postcondition), and a drift test asserting the vendored
task_classes / capabilities / auth_kinds match the conductor enums exactly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402
from routers import providers as providers_router  # noqa: E402

_LOGIN_STATUS_LITERALS = {"authenticated", "unauthenticated", "error", "unknown"}

# Expected dashboard_id <-> conductor id pairs (the mapping under test).
_EXPECTED_ID_PAIRS = {
    "claude_code_cli": "claude-cli",
    "ollama": "ollama-local",
    "codex_cli": "codex-cli",
    "cline": "cline-cli",
}


@pytest.fixture
def client(mock_auth) -> TestClient:  # noqa: ARG001
    return TestClient(server.app, raise_server_exceptions=False)


def _registry(client: TestClient) -> dict:
    resp = client.get("/api/providers/registry")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_dashboard_id(data: dict) -> dict[str, dict]:
    return {p["dashboard_id"]: p for p in data["providers"]}


# ---------------------------------------------------------------------------
# Contract shape
# ---------------------------------------------------------------------------
class TestContractShape:
    def test_top_level_keys(self, client: TestClient) -> None:
        data = _registry(client)
        for key in (
            "schema_version",
            "providers",
            "auth_kinds",
            "task_classes",
            "capabilities",
        ):
            assert key in data, f"missing top-level key {key!r}"

    def test_schema_version(self, client: TestClient) -> None:
        assert _registry(client)["schema_version"] == "1.0.0"

    def test_each_provider_has_full_contract(self, client: TestClient) -> None:
        required = {
            "id",
            "dashboard_id",
            "label",
            "execution_mode",
            "dispatch_mode",
            "auth_mode",
            "resource",
            "capabilities",
            "cost_per_task",
            "max_concurrency",
            "models",
            "models_endpoint",
            "login_status",
            "login_detail",
            "setup_hint",
            "experimental",
            "editable",
            "remote",
        }
        for prov in _registry(client)["providers"]:
            missing = required - prov.keys()
            assert not missing, f"{prov.get('dashboard_id')} missing {missing}"

    def test_field_types(self, client: TestClient) -> None:
        for prov in _registry(client)["providers"]:
            assert isinstance(prov["cost_per_task"], float)
            assert isinstance(prov["max_concurrency"], int)
            assert isinstance(prov["capabilities"], list)
            assert isinstance(prov["models"], list)
            assert all(isinstance(m, str) for m in prov["models"])
            assert prov["models_endpoint"] is None or isinstance(prov["models_endpoint"], str)
            assert isinstance(prov["experimental"], bool)
            assert isinstance(prov["editable"], bool)
            assert isinstance(prov["remote"], bool)

    def test_auth_mode_within_allowed(self, client: TestClient) -> None:
        allowed = {"none", "github_app", "api_key", "local"}
        for prov in _registry(client)["providers"]:
            assert prov["auth_mode"] in allowed

    def test_resource_within_allowed(self, client: TestClient) -> None:
        for prov in _registry(client)["providers"]:
            assert prov["resource"] in {"runner", "local"}


# ---------------------------------------------------------------------------
# ID-mapping correctness (underscore <-> hyphen)
# ---------------------------------------------------------------------------
class TestIdMapping:
    def test_known_pairs(self, client: TestClient) -> None:
        by_dash = _by_dashboard_id(_registry(client))
        for dashboard_id, conductor_id in _EXPECTED_ID_PAIRS.items():
            assert dashboard_id in by_dash, f"missing dashboard_id {dashboard_id}"
            assert by_dash[dashboard_id]["id"] == conductor_id, (
                f"{dashboard_id} should map to conductor id {conductor_id}, got {by_dash[dashboard_id]['id']}"
            )

    def test_dashboard_ids_unique(self, client: TestClient) -> None:
        ids = [p["dashboard_id"] for p in _registry(client)["providers"]]
        assert len(ids) == len(set(ids))

    def test_conductor_ids_unique(self, client: TestClient) -> None:
        ids = [p["id"] for p in _registry(client)["providers"]]
        assert len(ids) == len(set(ids))

    def test_covers_all_dashboard_providers(self, client: TestClient) -> None:
        from agent_remediation import PROVIDERS  # noqa: PLC0415

        registry_ids = {p["dashboard_id"] for p in _registry(client)["providers"]}
        assert set(PROVIDERS.keys()) <= registry_ids


# ---------------------------------------------------------------------------
# Ollama live-models fetch — injectable (LoD) and resilient (never 500)
# ---------------------------------------------------------------------------
class TestOllamaModels:
    def test_injected_fetcher_returns_two_models(self) -> None:
        def fake_fetch(_base_url: str) -> list[str]:
            return ["llama3:8b", "qwen2.5-coder:7b"]

        payload = providers_router.build_registry(ollama_models_fetcher=fake_fetch)
        by_dash = {p["dashboard_id"]: p for p in payload["providers"]}
        assert by_dash["ollama"]["models"] == ["llama3:8b", "qwen2.5-coder:7b"]
        assert by_dash["ollama"]["models_endpoint"] == "http://localhost:11434/api/tags"

    def test_connection_error_yields_empty_models_no_raise(self) -> None:
        def boom(_base_url: str) -> list[str]:
            raise ConnectionError("ollama unreachable")

        payload = providers_router.build_registry(ollama_models_fetcher=boom)
        by_dash = {p["dashboard_id"]: p for p in payload["providers"]}
        assert by_dash["ollama"]["models"] == []
        # Reachability failure must surface in login_status, never crash.
        assert by_dash["ollama"]["login_status"] in _LOGIN_STATUS_LITERALS

    def test_endpoint_does_not_500_when_ollama_down(self, client: TestClient) -> None:
        # The HTTP endpoint must be resilient even if the live fetch fails.
        resp = client.get("/api/providers/registry")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Model-selection support replaces hardcoded _PROVIDERS_WITH_MODEL_SELECTION
# ---------------------------------------------------------------------------
class TestModelSelection:
    def test_cli_providers_have_curated_models(self) -> None:
        def fake_fetch(_base_url: str) -> list[str]:
            return []

        payload = providers_router.build_registry(ollama_models_fetcher=fake_fetch)
        by_dash = {p["dashboard_id"]: p for p in payload["providers"]}
        assert by_dash["claude_code_cli"]["models"], "claude should expose curated models"
        assert by_dash["codex_cli"]["models"], "codex should expose curated models"


# ---------------------------------------------------------------------------
# login_status literal invariant (DbC postcondition)
# ---------------------------------------------------------------------------
class TestLoginStatusInvariant:
    def test_all_login_status_are_allowed_literals(self, client: TestClient) -> None:
        for prov in _registry(client)["providers"]:
            assert prov["login_status"] in _LOGIN_STATUS_LITERALS, (
                f"{prov['dashboard_id']} has illegal login_status {prov['login_status']!r}"
            )

    def test_login_detail_is_string(self, client: TestClient) -> None:
        for prov in _registry(client)["providers"]:
            assert isinstance(prov["login_detail"], str)

    def test_local_provider_authenticates_without_login(self) -> None:
        # Ollama is local; with reachable models it should not be "unauthenticated".
        def fake_fetch(_base_url: str) -> list[str]:
            return ["llama3:8b"]

        payload = providers_router.build_registry(ollama_models_fetcher=fake_fetch)
        by_dash = {p["dashboard_id"]: p for p in payload["providers"]}
        assert by_dash["ollama"]["login_status"] == "authenticated"


# ---------------------------------------------------------------------------
# Drift test: vendored enums must match the conductor source exactly
# ---------------------------------------------------------------------------
class TestConductorEnumDrift:
    """Vendored constants must equal the conductor StrEnum string values.

    We vendor (not import across repos) per the dashboard's decoupling rule,
    but this test reads the conductor source so drift is caught in CI when the
    conductor enums are reachable. When the sibling repo is not checked out the
    test skips rather than failing the dashboard build.
    """

    def _conductor_provider_path(self) -> Path | None:
        candidate = Path(__file__).resolve().parents[3] / "Repository_Management" / "conductor" / "provider.py"
        return candidate if candidate.exists() else None

    def _enum_values(self, source: str, enum_name: str) -> set[str]:
        import ast  # noqa: PLC0415

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == enum_name:
                values: set[str] = set()
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                        if isinstance(stmt.value.value, str):
                            values.add(stmt.value.value)
                return values
        raise AssertionError(f"enum {enum_name} not found in conductor source")

    def test_task_classes_match_conductor(self) -> None:
        from conductor_constants import TASK_CLASSES  # noqa: PLC0415

        path = self._conductor_provider_path()
        if path is None:
            pytest.skip("conductor source not checked out")
        expected = self._enum_values(path.read_text(encoding="utf-8"), "TaskClass")
        assert set(TASK_CLASSES) == expected

    def test_capabilities_match_conductor(self) -> None:
        from conductor_constants import CAPABILITIES  # noqa: PLC0415

        path = self._conductor_provider_path()
        if path is None:
            pytest.skip("conductor source not checked out")
        expected = self._enum_values(path.read_text(encoding="utf-8"), "Capability")
        assert set(CAPABILITIES) == expected

    def test_auth_kinds_match_conductor(self) -> None:
        from conductor_constants import AUTH_KINDS  # noqa: PLC0415

        path = self._conductor_provider_path()
        if path is None:
            pytest.skip("conductor source not checked out")
        expected = self._enum_values(path.read_text(encoding="utf-8"), "AuthMode")
        assert set(AUTH_KINDS) == expected

    def test_registry_enum_lists_are_vendored_constants(self, client: TestClient) -> None:
        from conductor_constants import (  # noqa: PLC0415
            AUTH_KINDS,
            CAPABILITIES,
            TASK_CLASSES,
        )

        data = _registry(client)
        assert set(data["task_classes"]) == set(TASK_CLASSES)
        assert set(data["capabilities"]) == set(CAPABILITIES)
        assert set(data["auth_kinds"]) == set(AUTH_KINDS)


# ---------------------------------------------------------------------------
# Back-compat: legacy endpoint must keep working (issue #810 reversibility)
# ---------------------------------------------------------------------------
class TestLegacyEndpointStillWorks:
    def test_legacy_agents_providers(self, client: TestClient) -> None:
        resp = client.get("/api/agents/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "availability" in data
