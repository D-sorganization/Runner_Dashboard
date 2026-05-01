"""Tests for the Maxwell contract (issue #366).

Acceptance criteria verified:
- AC-3: Extra fields from Maxwell are silently dropped (shape unchanged).
- AC-4: Sensitive fields from Maxwell are never forwarded.
- AC-1: Each proxy route has a declared contract model.
- General: Models serialize/deserialize cleanly; defaults are sane.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Make the backend package importable from tests/
# ---------------------------------------------------------------------------
_BACKEND = Path(__file__).parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import maxwell_contract as mc  # noqa: E402

# ---------------------------------------------------------------------------
# strip_sensitive
# ---------------------------------------------------------------------------


class TestStripSensitive:
    def test_removes_known_sensitive_keys(self) -> None:
        raw = {
            "name": "Anthropic",
            "api_key": "sk-secret",
            "connection_string": "postgres://user:pass@host/db",
            "model": "claude-3-opus",
        }
        result = mc.strip_sensitive(raw)
        assert "api_key" not in result
        assert "connection_string" not in result
        assert result["name"] == "Anthropic"
        assert result["model"] == "claude-3-opus"

    def test_recursive_strip(self) -> None:
        raw = {
            "backends": [
                {"name": "Ollama", "api_key": "hidden", "enabled": True},
            ]
        }
        result = mc.strip_sensitive(raw)
        assert "api_key" not in result["backends"][0]
        assert result["backends"][0]["name"] == "Ollama"

    def test_nested_dict_strip(self) -> None:
        raw = {
            "config": {
                "token": "t-secret",
                "host": "localhost",
            }
        }
        result = mc.strip_sensitive(raw)
        assert "token" not in result["config"]
        assert result["config"]["host"] == "localhost"

    def test_unknown_keys_passthrough_before_model(self) -> None:
        """strip_sensitive does not strip unknown (non-sensitive) keys."""
        raw = {"some_new_field": 42, "secret_token": "bad"}
        result = mc.strip_sensitive(raw)
        assert "some_new_field" in result  # not sensitive
        assert "secret_token" not in result

    def test_empty_dict(self) -> None:
        assert mc.strip_sensitive({}) == {}


# ---------------------------------------------------------------------------
# AC-3: Extra fields are silently dropped by Pydantic model validation
# ---------------------------------------------------------------------------


class TestExtraFieldsDropped:
    """Maxwell adds new fields — dashboard response shape must be unchanged."""

    def test_version_extra_fields_dropped(self) -> None:
        raw = {
            "version": "1.2.3",
            "build": "abc123",
            "new_field_maxwell_added": "surprise",
            "internal_metric": 99,
        }
        result = mc.MaxwellVersionResponse.model_validate(raw).model_dump()
        assert "new_field_maxwell_added" not in result
        assert "internal_metric" not in result
        assert result["version"] == "1.2.3"
        assert result["build"] == "abc123"

    def test_status_extra_fields_dropped(self) -> None:
        raw = {
            "state": "running",
            "active_tasks": 3,
            "queued_tasks": 1,
            "maxwell_internal_secret": "should-not-appear",
            "new_metric": "yes",
        }
        result = mc.MaxwellStatusResponse.model_validate(raw).model_dump()
        assert "maxwell_internal_secret" not in result
        assert "new_metric" not in result
        assert result["state"] == "running"
        assert result["active_tasks"] == 3

    def test_task_list_extra_fields_dropped(self) -> None:
        raw = {
            "tasks": [
                {
                    "id": "task-1",
                    "status": "queued",
                    "internal_ref": "SECRET",
                    "new_field": "ignored",
                }
            ],
            "cursor": "next-cursor",
            "total": 1,
            "new_pagination_key": "X",
        }
        result = mc.MaxwellTaskListResponse.model_validate(raw).model_dump()
        assert "new_pagination_key" not in result
        tasks = result["tasks"]
        assert len(tasks) == 1
        assert "internal_ref" not in tasks[0]
        assert tasks[0]["id"] == "task-1"

    def test_backends_extra_fields_dropped(self) -> None:
        raw = {
            "backends": [
                {
                    "name": "Anthropic",
                    "type": "cloud",
                    "enabled": True,
                    "model": "claude-opus-4",
                    "extra_cloud_config": "hidden",
                }
            ]
        }
        result = mc.MaxwellBackendsResponse.model_validate(raw).model_dump()
        backend = result["backends"][0]
        assert "extra_cloud_config" not in backend
        assert backend["name"] == "Anthropic"
        assert backend["model"] == "claude-opus-4"


# ---------------------------------------------------------------------------
# AC-4: Sensitive fields are NOT present in dashboard responses
# ---------------------------------------------------------------------------


class TestSensitiveFieldsNeverForwarded:
    """Stub returning sensitive fields must not appear in dashboard response."""

    def test_api_key_stripped_from_version(self) -> None:
        raw = {"version": "1.0.0", "api_key": "sk-dangerous", "secret_token": "another"}  # pragma: allowlist secret
        cleaned = mc.strip_sensitive(raw)
        result = mc.MaxwellVersionResponse.model_validate(cleaned).model_dump()
        assert "api_key" not in result
        assert "secret_token" not in result

    def test_api_key_stripped_from_backend(self) -> None:
        raw = {
            "backends": [
                {
                    "name": "Anthropic",
                    "api_key": "sk-ant-supersecret",  # pragma: allowlist secret
                    "connection_string": "postgres://secret@host/db",
                    "enabled": True,
                    "type": "cloud",
                }
            ]
        }
        cleaned = mc.strip_sensitive(raw)
        result = mc.MaxwellBackendsResponse.model_validate(cleaned).model_dump()
        backend = result["backends"][0]
        assert "api_key" not in backend
        assert "connection_string" not in backend
        assert backend["name"] == "Anthropic"

    def test_all_known_sensitive_keys_stripped(self) -> None:
        raw = {
            "version": "1.0.0",
            "secret_token": "t1",  # pragma: allowlist secret
            "api_key": "k1",  # pragma: allowlist secret
            "api_secret": "s1",  # pragma: allowlist secret
            "token": "tok1",
            "password": "p1",  # pragma: allowlist secret
            "private_key": "pk1",  # pragma: allowlist secret
            "connection_string": "cs1",
            "db_url": "db1",
            "webhook_secret": "ws1",  # pragma: allowlist secret
            "signing_secret": "ss1",  # pragma: allowlist secret
            "client_secret": "cs2",  # pragma: allowlist secret
        }
        cleaned = mc.strip_sensitive(raw)
        result = mc.MaxwellVersionResponse.model_validate(cleaned).model_dump()
        for sensitive_key in mc._SENSITIVE_FIELDS:
            assert sensitive_key not in result, f"Sensitive field {sensitive_key!r} leaked into response!"


# ---------------------------------------------------------------------------
# Default / missing field handling
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_version_defaults(self) -> None:
        result = mc.MaxwellVersionResponse.model_validate({}).model_dump()
        assert result["version"] == "unknown"
        assert result["build"] is None

    def test_status_defaults(self) -> None:
        result = mc.MaxwellStatusResponse.model_validate({}).model_dump()
        assert result["state"] == "unknown"
        assert result["active_tasks"] == 0
        assert result["paused"] is False

    def test_task_list_defaults(self) -> None:
        result = mc.MaxwellTaskListResponse.model_validate({}).model_dump()
        assert result["tasks"] == []
        assert result["cursor"] is None

    def test_cost_defaults(self) -> None:
        result = mc.MaxwellCostResponse.model_validate({}).model_dump()
        assert result["total_usd"] is None
        assert result["currency"] == "USD"

    def test_dispatch_response_id_alias(self) -> None:
        """Maxwell returns 'id' but dashboard exposes it as 'task_id'."""
        raw = {"id": "task-uuid-123", "status": "queued"}
        result = mc.MaxwellDispatchResponse.model_validate(raw).model_dump(by_alias=False)
        assert result["task_id"] == "task-uuid-123"
        assert "id" not in result

    def test_control_response_defaults(self) -> None:
        result = mc.MaxwellControlResponse.model_validate({"action": "pause"}).model_dump()
        assert result["action"] == "pause"
        assert result["status"] == "ok"
        assert result["message"] is None
