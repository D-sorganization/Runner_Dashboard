"""Tests for /api/runners/label-guidance and /api/runners/label-audit endpoints.

Issue #757 — workflow routing guidance for NVMe, HDD, Docker, and bulk labels.
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


@pytest.fixture
def client(mock_auth) -> TestClient:  # noqa: ARG001
    """Create a test client for the FastAPI app."""
    return TestClient(server.app)


# ---------------------------------------------------------------------------
# /api/runners/label-guidance
# ---------------------------------------------------------------------------


class TestLabelGuidance:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/runners/label-guidance")
        assert resp.status_code == 200

    def test_response_has_taxonomy_key(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-guidance").json()
        assert "taxonomy" in data

    def test_taxonomy_contains_expected_labels(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-guidance").json()
        taxonomy = data["taxonomy"]
        expected = {
            "d-sorg-fleet-nvme",
            "d-sorg-fleet-fast-io",
            "d-sorg-fleet-docker",
            "d-sorg-fleet-bulk",
        }
        assert expected.issubset(set(taxonomy.keys()))

    def test_each_label_has_required_fields(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-guidance").json()
        for label, info in data["taxonomy"].items():
            assert "purpose" in info, f"{label} missing 'purpose'"
            assert "workload" in info, f"{label} missing 'workload'"
            assert "avoid_for" in info, f"{label} missing 'avoid_for'"
            assert "runs_on_snippet" in info, f"{label} missing 'runs_on_snippet'"

    def test_runs_on_snippet_is_string(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-guidance").json()
        for label, info in data["taxonomy"].items():
            assert isinstance(info["runs_on_snippet"], str), f"{label} snippet must be str"

    def test_response_has_neutral_labels(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-guidance").json()
        assert "neutral_labels" in data
        assert isinstance(data["neutral_labels"], list)
        assert "d-sorg-fleet" in data["neutral_labels"]

    def test_response_has_workflow_classes(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-guidance").json()
        assert "workflow_classes" in data
        classes = data["workflow_classes"]
        assert "bulk" in classes
        assert "docker" in classes
        assert "fast-io" in classes

    def test_workflow_class_has_recommended_labels(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-guidance").json()
        for cls, info in data["workflow_classes"].items():
            assert "recommended_labels" in info, f"class {cls} missing recommended_labels"
            assert "description" in info, f"class {cls} missing description"

    def test_response_has_generated_at(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-guidance").json()
        assert "generated_at" in data
        assert isinstance(data["generated_at"], str)


# ---------------------------------------------------------------------------
# /api/runners/label-audit
# ---------------------------------------------------------------------------


class TestLabelAudit:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/runners/label-audit")
        assert resp.status_code == 200

    def test_response_has_ok_field(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-audit").json()
        assert "ok" in data
        assert isinstance(data["ok"], bool)

    def test_response_has_violations_list(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-audit").json()
        assert "violations" in data
        assert isinstance(data["violations"], list)

    def test_response_has_recommendations_list(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-audit").json()
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)

    def test_response_has_policy_source(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-audit").json()
        assert "policy_source" in data

    def test_response_has_generated_at(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-audit").json()
        assert "generated_at" in data

    def test_audit_does_not_require_wsl(self, client: TestClient) -> None:
        """The audit must not depend on WSL being online; it reads policy from disk."""
        resp = client.get("/api/runners/label-audit")
        assert resp.status_code == 200
        data = resp.json()
        # If policy file is missing, ok=False and policy_errors is set — still not a 5xx.
        assert "ok" in data

    def test_policy_errors_field_is_list(self, client: TestClient) -> None:
        data = client.get("/api/runners/label-audit").json()
        assert "policy_errors" in data
        assert isinstance(data["policy_errors"], list)
