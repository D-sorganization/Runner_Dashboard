"""Tests for the Maxwell contract (issue #366).

Acceptance criteria verified:
- AC-3: Extra fields from Maxwell are silently dropped (shape unchanged).
- AC-4: Sensitive fields from Maxwell are never forwarded.
- AC-1: Each proxy route has a declared contract model.
- General: Models serialize/deserialize cleanly; defaults are sane.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Make the backend package importable from tests/
# ---------------------------------------------------------------------------
_BACKEND = Path(__file__).parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import maxwell_contract as mc  # noqa: E402

_CONTRACTS = Path(__file__).parent / "contracts"
_MAXWELL_OPENAPI = _CONTRACTS / "maxwell_openapi.json"


def _load_maxwell_openapi() -> dict:
    return json.loads(_MAXWELL_OPENAPI.read_text(encoding="utf-8"))


def _resolve_schema(openapi: dict, schema: dict) -> dict:
    ref = schema.get("$ref")
    if not ref:
        return schema
    name = ref.rsplit("/", 1)[-1]
    return openapi["components"]["schemas"][name]


def _response_schema(openapi: dict, path: str, method: str = "get", status: str = "200") -> dict:
    return _resolve_schema(
        openapi,
        openapi["paths"][path][method]["responses"][status]["content"]["application/json"]["schema"],
    )


def _schema_payload(openapi: dict, schema: dict, *, field_name: str = "") -> object:
    schema = _resolve_schema(openapi, schema)
    if "anyOf" in schema:
        non_null = next((item for item in schema["anyOf"] if item.get("type") != "null"), schema["anyOf"][0])
        return _schema_payload(openapi, non_null, field_name=field_name)
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]

    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = schema.get("required", list(properties))
        if not properties:
            additional = schema.get("additionalProperties", {})
            if additional is True:
                return {}
            if not isinstance(additional, dict):
                additional = {}
            if additional.get("type") == "integer":
                return {"running": 1, "queued": 0, "completed": 1, "failed": 0}
            if additional.get("type") == "number":
                return {"sample": 1.0}
            return {}
        return {
            name: _schema_payload(openapi, properties[name], field_name=name) for name in required if name in properties
        }
    if schema_type == "array":
        return [_schema_payload(openapi, schema.get("items", {}), field_name=field_name)]
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return True
    if schema_type == "string":
        samples = {
            "action": "pause",
            "contract": mc.EXPECTED_CONTRACT_VERSION,
            "created_at": "2026-06-12T00:00:00Z",
            "daemon": "3.1.0",
            "gate": "open",
            "generated_at": "2026-06-12T00:00:00Z",
            "pipeline_state": "running",
            "previous_state": "running",
            "queued_at": "2026-06-12T00:00:00Z",
            "sandbox": "enabled",
            "status": "running",
            "task_id": "t-1",
        }
        return samples.get(field_name, f"{field_name or 'value'}-sample")
    return None


def _maxwell_contract_payloads() -> dict[str, dict]:
    openapi = _load_maxwell_openapi()
    task_list = _schema_payload(openapi, _response_schema(openapi, "/api/tasks"))
    return {
        "version": _schema_payload(openapi, _response_schema(openapi, "/api/version")),
        "status": _schema_payload(openapi, _response_schema(openapi, "/api/status")),
        "status_v2": _schema_payload(openapi, _response_schema(openapi, "/api/v2/status")),
        "task_list": task_list,
        "task_summary": task_list["tasks"][0],
        "task_detail": _schema_payload(openapi, _response_schema(openapi, "/api/tasks/{task_id}")),
        "dispatch": _schema_payload(openapi, _response_schema(openapi, "/api/dispatch", "post", "202")),
        "control": _schema_payload(openapi, _response_schema(openapi, "/api/control/{action}", "post")),
        # These MD endpoints still publish generic object schemas. Keep explicit
        # producer-observed payloads until MD gives them typed OpenAPI responses.
        "workers": {"worker_count": 2, "queue_depth": 0},
    }


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
        # Real MD shape is {daemon, contract} (#956).
        raw = {
            "daemon": "1.2.3",
            "contract": "2.0.0",
            "new_field_maxwell_added": "surprise",
            "internal_metric": 99,
        }
        result = mc.MaxwellVersionResponse.model_validate(raw).model_dump()
        assert "new_field_maxwell_added" not in result
        assert "internal_metric" not in result
        assert result["daemon"] == "1.2.3"
        # `version` mirrors `daemon` for the existing frontend.
        assert result["version"] == "1.2.3"
        assert result["contract"] == "2.0.0"
        assert result["contract_compatible"] is True

    def test_status_extra_fields_dropped(self) -> None:
        # Real MD shape is {pipeline_state, active_task_id, gate, sandbox} (#955).
        raw = {
            "pipeline_state": "running",
            "active_task_id": "task-9",
            "gate": "open",
            "sandbox": "enabled",
            "maxwell_internal_secret": "should-not-appear",
            "new_metric": "yes",
        }
        result = mc.MaxwellStatusResponse.model_validate(raw).model_dump()
        assert "maxwell_internal_secret" not in result
        assert "new_metric" not in result
        assert result["state"] == "running"
        # active_task_id present → at least one active task.
        assert result["active_tasks"] == 1

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
            "next_cursor": "next-cursor",
            "total": 1,
            "new_pagination_key": "X",
        }
        result = mc.MaxwellTaskListResponse.model_validate(raw).model_dump()
        assert "new_pagination_key" not in result
        # Pagination is keyed next_cursor now, not cursor (issue #961).
        assert "cursor" not in result
        assert result["next_cursor"] == "next-cursor"
        tasks = result["tasks"]
        assert len(tasks) == 1
        assert "internal_ref" not in tasks[0]
        assert tasks[0]["id"] == "task-1"
        # MaxwellTaskItem only models MD's real TaskSummary fields.
        assert set(tasks[0].keys()) == {"id", "status", "created_at"}

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

    def test_backends_accept_daemon_string_list_shape(self) -> None:
        raw = {"backends": ["openai", "ollama"]}

        result = mc.MaxwellBackendsResponse.model_validate(raw).model_dump()

        assert result["backends"] == [
            {"name": "openai", "type": "unknown", "enabled": True, "model": None, "status": None},
            {"name": "ollama", "type": "unknown", "enabled": True, "model": None, "status": None},
        ]


# ---------------------------------------------------------------------------
# AC-4: Sensitive fields are NOT present in dashboard responses
# ---------------------------------------------------------------------------


class TestSensitiveFieldsNeverForwarded:
    """Stub returning sensitive fields must not appear in dashboard response."""

    def test_api_key_stripped_from_version(self) -> None:
        raw = {
            "daemon": "1.0.0",
            "contract": "2.0.0",
            "api_key": "sk-dangerous",  # pragma: allowlist secret
            "secret_token": "another",  # pragma: allowlist secret
        }
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
            "daemon": "1.0.0",
            "contract": "2.0.0",
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
    def test_task_list_defaults(self) -> None:
        result = mc.MaxwellTaskListResponse.model_validate({}).model_dump()
        assert result["tasks"] == []
        assert result["next_cursor"] is None
        assert "cursor" not in result

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


# ---------------------------------------------------------------------------
# Real MD-shape mapping + fail-loud on drift (issues #955, #956, #958)
# ---------------------------------------------------------------------------


import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402


class TestVersionContractNegotiation:
    """#956 — /api/version maps {daemon, contract} and negotiates compatibility."""

    def test_compatible_contract(self) -> None:
        result = mc.MaxwellVersionResponse.model_validate({"daemon": "3.1.0", "contract": "2.4.1"}).model_dump()
        assert result["version"] == "3.1.0"
        assert result["contract"] == "2.4.1"
        assert result["contract_compatible"] is True  # major 2 == expected major 2

    def test_incompatible_major_contract(self) -> None:
        result = mc.MaxwellVersionResponse.model_validate({"daemon": "9.0.0", "contract": "3.0.0"}).model_dump()
        assert result["contract_compatible"] is False

    def test_missing_contract_fails_loud(self) -> None:
        with pytest.raises(ValidationError):
            mc.MaxwellVersionResponse.model_validate({"daemon": "1.0.0"})

    def test_missing_daemon_fails_loud(self) -> None:
        with pytest.raises(ValidationError):
            mc.MaxwellVersionResponse.model_validate({"contract": "2.0.0"})


class TestStatusMapping:
    """#955 — /api/status maps pipeline_state→state; v2 counts refine tallies."""

    def test_pipeline_state_maps_to_state(self) -> None:
        result = mc.MaxwellStatusResponse.model_validate(
            {"pipeline_state": "idle", "active_task_id": None, "gate": "open", "sandbox": "enabled"}
        ).model_dump()
        assert result["state"] == "idle"
        assert result["active_tasks"] == 0
        assert result["paused"] is False

    def test_paused_derived(self) -> None:
        result = mc.MaxwellStatusResponse.model_validate({"pipeline_state": "paused"}).model_dump()
        assert result["paused"] is True

    def test_missing_pipeline_state_fails_loud(self) -> None:
        # The old imaginary {state, active_tasks} shape must now be rejected, not
        # silently defaulted to "unknown / 0 tasks".
        with pytest.raises(ValidationError):
            mc.MaxwellStatusResponse.model_validate({"state": "running", "active_tasks": 3})

    def test_v2_counts_merge(self) -> None:
        status = mc.MaxwellStatusResponse.model_validate({"pipeline_state": "running", "active_task_id": "t1"})
        v2 = mc.MaxwellStatusV2Response.model_validate(
            {"counts": {"running": 2, "queued": 5, "completed": 10, "failed": 1}}
        )
        status.merge_v2_counts(v2)
        d = status.model_dump()
        assert d["active_tasks"] == 2
        assert d["queued_tasks"] == 5
        assert d["completed_tasks"] == 10
        assert d["failed_tasks"] == 1

    def test_v2_missing_counts_fails_loud(self) -> None:
        with pytest.raises(ValidationError):
            mc.MaxwellStatusV2Response.model_validate({"running": []})


class TestWorkersMapping:
    """#958 — /api/v1/workers maps {worker_count, queue_depth}."""

    def test_worker_count_maps_to_total(self) -> None:
        result = mc.MaxwellWorkersResponse.model_validate({"worker_count": 4, "queue_depth": 7}).model_dump()
        assert result["worker_count"] == 4
        assert result["total"] == 4
        assert result["queue_depth"] == 7
        assert result["workers"] == []

    def test_missing_worker_count_fails_loud(self) -> None:
        # The old {workers, total} shape (zero overlap with MD) must be rejected.
        with pytest.raises(ValidationError):
            mc.MaxwellWorkersResponse.model_validate({"workers": [], "total": 0})


class TestTaskContractAlignment:
    """#961 — task list/detail align with MD's real TaskSummary/TaskDetail shape."""

    def test_task_list_maps_md_next_cursor(self) -> None:
        # MD's real list shape: tasks of {id,status,created_at} + next_cursor.
        raw = {
            "tasks": [{"id": "t-1", "status": "running", "created_at": "2026-06-12T00:00:00Z"}],
            "next_cursor": None,
            "total": 1,
        }
        result = mc.MaxwellTaskListResponse.model_validate(raw).model_dump()
        assert result["next_cursor"] is None
        assert result["tasks"][0]["status"] == "running"

    def test_task_item_missing_status_fails_loud(self) -> None:
        # status is now required — a defaulted "unknown" row is impossible (#961).
        with pytest.raises(ValidationError):
            mc.MaxwellTaskItem.model_validate({"id": "t-1"})

    def test_task_item_missing_id_fails_loud(self) -> None:
        with pytest.raises(ValidationError):
            mc.MaxwellTaskItem.model_validate({"status": "queued"})

    def test_task_detail_maps_md_transcript_artifacts(self) -> None:
        # MD's real detail shape: {id,status,created_at,transcript,artifacts}.
        raw = {
            "id": "t-9",
            "status": "completed",
            "created_at": "2026-06-12T00:00:00Z",
            "transcript": [{"role": "system", "text": "hi"}],
            "artifacts": ["out.log"],
        }
        result = mc.MaxwellTaskDetailResponse.model_validate(raw).model_dump()
        assert result["transcript"] == [{"role": "system", "text": "hi"}]
        assert result["artifacts"] == ["out.log"]
        # Phantom fields RD invented are gone.
        for phantom in ("updated_at", "type", "priority", "tags", "error", "result_summary"):
            assert phantom not in result

    def test_task_detail_defaults_transcript_artifacts_empty(self) -> None:
        # MD returns [] today; the model must accept their absence and default.
        result = mc.MaxwellTaskDetailResponse.model_validate({"id": "t-1", "status": "queued"}).model_dump()
        assert result["transcript"] == []
        assert result["artifacts"] == []

    def test_task_detail_missing_status_fails_loud(self) -> None:
        with pytest.raises(ValidationError):
            mc.MaxwellTaskDetailResponse.model_validate({"id": "t-1"})


# ---------------------------------------------------------------------------
# #960 — consumer-driven contract: derive RD's canonical payloads from MD's
# generated OpenAPI snapshot. If MD renames a discriminating field in the
# precise schemas below, the corresponding `*_field_rename_fails_loud` test
# turns red here, surfacing drift on the consumer side before merge.
# ---------------------------------------------------------------------------

MD_CANONICAL: dict[str, dict] = _maxwell_contract_payloads()


class TestConsumerDrivenContract:
    """#960 — RD models validate against MD's real shapes, not hand-written ones."""

    def test_openapi_snapshot_is_vendored(self) -> None:
        openapi = _load_maxwell_openapi()
        assert openapi["paths"]["/api/status"]["get"]["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/StatusResponse")
        assert "StatusResponse" in openapi["components"]["schemas"]

    def test_version_accepts_canonical_md_shape(self) -> None:
        mc.MaxwellVersionResponse.model_validate(MD_CANONICAL["version"])

    def test_status_accepts_canonical_md_shape(self) -> None:
        mc.MaxwellStatusResponse.model_validate(MD_CANONICAL["status"])

    def test_status_v2_accepts_canonical_md_shape(self) -> None:
        mc.MaxwellStatusV2Response.model_validate(MD_CANONICAL["status_v2"])

    def test_task_summary_accepts_canonical_md_shape(self) -> None:
        mc.MaxwellTaskItem.model_validate(MD_CANONICAL["task_summary"])

    def test_task_list_accepts_canonical_md_shape(self) -> None:
        mc.MaxwellTaskListResponse.model_validate(MD_CANONICAL["task_list"])

    def test_task_detail_accepts_canonical_md_shape(self) -> None:
        mc.MaxwellTaskDetailResponse.model_validate(MD_CANONICAL["task_detail"])

    def test_workers_accepts_canonical_md_shape(self) -> None:
        mc.MaxwellWorkersResponse.model_validate(MD_CANONICAL["workers"])

    def test_control_accepts_canonical_md_shape(self) -> None:
        mc.MaxwellControlResponse.model_validate(MD_CANONICAL["control"])

    def test_dispatch_accepts_canonical_md_shape(self) -> None:
        mc.MaxwellDispatchResponse.model_validate(MD_CANONICAL["dispatch"])

    @pytest.mark.parametrize(
        ("shape_key", "discriminator", "model"),
        [
            ("version", "contract", "MaxwellVersionResponse"),
            ("status", "pipeline_state", "MaxwellStatusResponse"),
            ("status_v2", "counts", "MaxwellStatusV2Response"),
            ("task_summary", "status", "MaxwellTaskItem"),
            ("task_detail", "status", "MaxwellTaskDetailResponse"),
            ("dispatch", "task_id", "MaxwellDispatchResponse"),
            ("workers", "worker_count", "MaxwellWorkersResponse"),
            ("control", "action", "MaxwellControlResponse"),
        ],
    )
    def test_discriminating_field_rename_fails_loud(self, shape_key: str, discriminator: str, model: str) -> None:
        """If MD renames a required discriminating field, the consumer model rejects it.

        Simulates upstream drift by renaming the discriminator to ``<name>_renamed``;
        a defaulted/silent acceptance would mean drift is invisible (the #960 root
        cause). The model must raise ValidationError instead.
        """
        renamed = dict(MD_CANONICAL[shape_key])
        renamed[f"{discriminator}_renamed"] = renamed.pop(discriminator)
        model_cls = getattr(mc, model)
        with pytest.raises(ValidationError):
            model_cls.model_validate(renamed)
