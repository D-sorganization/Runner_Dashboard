"""Hub-to-node remote execution contract primitives."""

from __future__ import annotations

import datetime as _dt_mod
import enum
import ipaddress
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

SCHEMA_VERSION = "remote-execution-envelope.v1"
MAX_TIMEOUT_SECONDS = 3600
PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


class _StrEnum(str, enum.Enum):  # noqa: UP042
    """Python 3.10 compatible StrEnum."""

    pass


class RemoteExecutionAccess(_StrEnum):
    READ_ONLY = "read_only"
    PRIVILEGED = "privileged"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _ensure_dict(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    raise TypeError("payload must be a mapping")


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if value is None:
        raise ValueError(f"{key} is required")
    text = str(value)
    if not text.strip():
        raise ValueError(f"{key} is required")
    return text


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _normalize_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be an integer") from exc
    if timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    if timeout > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must not exceed {MAX_TIMEOUT_SECONDS}")
    return timeout


def _host_is_private(hostname: str) -> bool:
    host = hostname.strip().strip("[]")
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        ip_addr = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(".ts.net") or host.endswith(".localhost")
    return any(ip_addr in network for network in PRIVATE_NETWORKS)


def _url_is_private(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname
    return parsed.scheme in {"http", "https"} and hostname is not None and _host_is_private(hostname)


def _inventory_index(registry: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entry in (registry or {}).get("machines", []):
        if not isinstance(entry, dict):
            continue
        for key in [entry.get("name", ""), *entry.get("aliases", [])]:
            token = _normalize_token(str(key))
            if token:
                index[token] = entry
    return index


def _resolve_inventory_entry(target: str, registry: dict[str, Any] | None) -> dict[str, Any] | None:
    entry = _inventory_index(registry).get(_normalize_token(target))
    return dict(entry) if entry is not None else None


def _resolve_private_target_url(entry: dict[str, Any]) -> str:
    candidates = [str(entry.get("dashboard_url", "")).strip()]
    for node in entry.get("tailscale_nodes", []):
        if isinstance(node, dict) and str(node.get("ip", "")).strip():
            port = str(node.get("port", "8321")).strip() or "8321"
            candidates.append(f"http://{str(node['ip']).strip()}:{port}")
    for url in candidates:
        if url and _url_is_private(url):
            return url
    raise ValueError("target must resolve to a private fleet-network endpoint")


def _resolve_target(target: str, registry: dict[str, Any] | None) -> RemoteExecutionTarget | None:
    entry = _resolve_inventory_entry(target, registry)
    if entry is None:
        return None
    return RemoteExecutionTarget(
        machine_name=str(entry.get("name", target)),
        dashboard_url=_resolve_private_target_url(entry),
        role=str(entry.get("role", "node")),
        registry_entry=entry,
    )


@dataclass(frozen=True, slots=True)
class RemoteExecutionConfirmation:
    approved_by: str
    approved_at: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class RemoteExecutionAction:
    name: str
    access: RemoteExecutionAccess
    description: str
    prototype_command: tuple[str, ...]
    requires_confirmation: bool = False


@dataclass(frozen=True, slots=True)
class RemoteExecutionEnvelope:
    action: str
    source: str
    target: str
    requested_by: str
    artifact_ref: str = ""
    rollback_point: str = ""
    timeout_seconds: int = 600
    dry_run: bool = False
    capture_stdout: bool = True
    capture_stderr: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    confirmation: RemoteExecutionConfirmation | None = None
    envelope_id: str = field(default_factory=lambda: uuid4().hex)
    schema_version: str = SCHEMA_VERSION
    issued_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.confirmation is not None:
            data["confirmation"] = asdict(self.confirmation)
        return data


@dataclass(frozen=True, slots=True)
class RemoteExecutionTarget:
    machine_name: str
    dashboard_url: str
    role: str
    registry_entry: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RemoteExecutionPlan:
    envelope_id: str
    action: str
    source: str
    target: RemoteExecutionTarget
    artifact_ref: str
    rollback_point: str
    command: tuple[str, ...]
    timeout_seconds: int
    dry_run: bool
    capture_stdout: bool
    capture_stderr: bool
    requested_by: str


@dataclass(frozen=True, slots=True)
class RemoteExecutionResult:
    envelope_id: str
    action: str
    target: str
    requested_by: str
    command: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    recorded_at: str


@dataclass(frozen=True, slots=True)
class RemoteExecutionValidationResult:
    accepted: bool
    reason: str
    action: RemoteExecutionAction | None
    target: RemoteExecutionTarget | None
    confirmation_required: bool


ALLOWLISTED_OPERATIONS: dict[str, RemoteExecutionAction] = {
    "node.status": RemoteExecutionAction(
        name="node.status",
        access=RemoteExecutionAccess.READ_ONLY,
        description="Read the current node service and health state.",
        prototype_command=("bash", "-lc", "systemctl status runner-dashboard"),
    ),
    "node.deploy_artifact": RemoteExecutionAction(
        name="node.deploy_artifact",
        access=RemoteExecutionAccess.PRIVILEGED,
        description="Deploy an artifact to a node and restart the service.",
        prototype_command=("bash", "runner-dashboard/deploy/update-deployed.sh"),
        requires_confirmation=True,
    ),
    "node.rollback_artifact": RemoteExecutionAction(
        name="node.rollback_artifact",
        access=RemoteExecutionAccess.PRIVILEGED,
        description="Roll a node back to a prior fleet deployment point.",
        prototype_command=("bash", "runner-dashboard/deploy/rollback.sh"),
        requires_confirmation=True,
    ),
    "node.restart_service": RemoteExecutionAction(
        name="node.restart_service",
        access=RemoteExecutionAccess.PRIVILEGED,
        description="Restart the dashboard service on the target node.",
        prototype_command=("sudo", "systemctl", "restart", "runner-dashboard"),
        requires_confirmation=True,
    ),
}


def get_operation(operation_name: str) -> RemoteExecutionAction | None:
    return ALLOWLISTED_OPERATIONS.get(operation_name)


def command_preview(operation_name: str, payload: dict[str, Any] | None = None) -> tuple[str, ...]:
    operation = get_operation(operation_name)
    if operation is None:
        raise KeyError(operation_name)
    payload = _ensure_dict(payload)
    command = list(operation.prototype_command)
    if operation_name == "node.deploy_artifact":
        artifact_ref = str(payload.get("artifact_ref", "")).strip()
        if artifact_ref:
            command.append(artifact_ref)
    elif operation_name == "node.rollback_artifact":
        rollback_point = str(payload.get("rollback_point", "")).strip()
        if rollback_point:
            command.extend(["--rollback-to", rollback_point])
    return tuple(command)


def build_envelope(
    *,
    action: str,
    source: str,
    target: str,
    requested_by: str,
    artifact_ref: str = "",
    rollback_point: str = "",
    timeout_seconds: int = 600,
    dry_run: bool = False,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
    payload: dict[str, Any] | None = None,
    confirmation: RemoteExecutionConfirmation | None = None,
) -> RemoteExecutionEnvelope:
    return RemoteExecutionEnvelope(
        action=action,
        source=source,
        target=target,
        requested_by=requested_by,
        artifact_ref=artifact_ref,
        rollback_point=rollback_point,
        timeout_seconds=_normalize_timeout(timeout_seconds),
        dry_run=dry_run,
        capture_stdout=capture_stdout,
        capture_stderr=capture_stderr,
        payload=_ensure_dict(payload),
        confirmation=confirmation,
    )


def validate_envelope(
    envelope: RemoteExecutionEnvelope, registry: dict[str, Any] | None = None
) -> RemoteExecutionValidationResult:
    if envelope.schema_version != SCHEMA_VERSION:
        return RemoteExecutionValidationResult(
            False,
            f"unsupported schema version: {envelope.schema_version}",
            None,
            None,
            False,
        )

    if not envelope.action:
        return RemoteExecutionValidationResult(False, "action is required", None, None, False)

    action = get_operation(envelope.action)
    if action is None:
        return RemoteExecutionValidationResult(
            False, f"action is not allowlisted: {envelope.action}", None, None, False
        )

    if not envelope.source.strip():
        return RemoteExecutionValidationResult(False, "source is required", action, None, action.requires_confirmation)
    if not envelope.target.strip():
        return RemoteExecutionValidationResult(False, "target is required", action, None, action.requires_confirmation)
    if not envelope.requested_by.strip():
        return RemoteExecutionValidationResult(
            False,
            "requested_by is required",
            action,
            None,
            action.requires_confirmation,
        )

    try:
        target = _resolve_target(envelope.target, registry)
    except ValueError as exc:
        return RemoteExecutionValidationResult(False, str(exc), action, None, action.requires_confirmation)

    if target is None:
        return RemoteExecutionValidationResult(
            False,
            f"target is not in the fleet inventory: {envelope.target}",
            action,
            None,
            action.requires_confirmation,
        )

    if action.access is RemoteExecutionAccess.PRIVILEGED and envelope.confirmation is None:
        return RemoteExecutionValidationResult(
            False,
            f"confirmation required for privileged action: {action.name}",
            action,
            target,
            True,
        )

    # Explicit narrowing for type checker and runtime safety.
    # The guard above only fires for PRIVILEGED actions; read-only actions may
    # legally omit confirmation. We narrow here so the remaining code can
    # safely access confirmation fields without runtime errors or assert
    # statements (which are stripped in optimised Python mode).
    confirmation = envelope.confirmation
    if confirmation is None:
        return RemoteExecutionValidationResult(
            False,
            "confirmation is required but missing",
            action,
            target,
            True,
        )

    if action.access is RemoteExecutionAccess.PRIVILEGED and not confirmation.approved_by.strip():
        return RemoteExecutionValidationResult(False, "confirmation must record approved_by", action, target, True)
    if action.access is RemoteExecutionAccess.PRIVILEGED and not confirmation.approved_at.strip():
        return RemoteExecutionValidationResult(False, "confirmation must record approved_at", action, target, True)
    if action.name == "node.deploy_artifact" and not envelope.artifact_ref.strip():
        return RemoteExecutionValidationResult(
            False,
            "artifact_ref is required for node.deploy_artifact",
            action,
            target,
            True,
        )
    if action.name == "node.rollback_artifact" and not envelope.rollback_point.strip():
        return RemoteExecutionValidationResult(
            False,
            "rollback_point is required for node.rollback_artifact",
            action,
            target,
            True,
        )

    return RemoteExecutionValidationResult(True, "accepted", action, target, action.requires_confirmation)


def build_execution_plan(
    envelope: RemoteExecutionEnvelope, registry: dict[str, Any] | None = None
) -> RemoteExecutionPlan:
    validation = validate_envelope(envelope, registry=registry)
    if not validation.accepted or validation.action is None or validation.target is None:
        raise ValueError(validation.reason)
    payload = dict(envelope.payload)
    if envelope.artifact_ref:
        payload.setdefault("artifact_ref", envelope.artifact_ref)
    if envelope.rollback_point:
        payload.setdefault("rollback_point", envelope.rollback_point)
    return RemoteExecutionPlan(
        envelope_id=envelope.envelope_id,
        action=envelope.action,
        source=envelope.source,
        target=validation.target,
        artifact_ref=envelope.artifact_ref,
        rollback_point=envelope.rollback_point,
        command=command_preview(envelope.action, payload),
        timeout_seconds=envelope.timeout_seconds,
        dry_run=envelope.dry_run,
        capture_stdout=envelope.capture_stdout,
        capture_stderr=envelope.capture_stderr,
        requested_by=envelope.requested_by,
    )


def classify_result(result: RemoteExecutionResult) -> str:
    if result.timed_out:
        return "timed_out"
    if result.exit_code is None:
        return "unknown_exit_code"
    if result.exit_code == 0:
        return "succeeded"
    return f"exit_{result.exit_code}"
