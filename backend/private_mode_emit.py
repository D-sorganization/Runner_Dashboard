"""Private Mode PM-4 ``emit_shared()`` choke point and per-surface projections (issue #3254, epic #3249).

* Flag off (:data:`private_mode.DISABLED`): effective level is ``Level.PUBLIC`` and payload
  flows through ``staff.redaction.redact_value`` unchanged from pre-Private-Mode behaviour.
* ``PUBLIC`` / ``INTERNAL``: payload is redacted via ``staff.redaction.redact_value`` (DRY).
* ``SENSITIVE``: projects **before any regex** runs to a typed, allowlisted per-surface
  metadata projection with opaque identifiers (``opaque:<hex>``). Content hashes are omitted
  by default; an optional keyed HMAC-SHA256 is computed only when ``include_content_mac=True``
  and a non-empty ``mac_key`` is supplied (raw SHA-256 content hashes are forbidden).
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from private_mode import (
    DISABLED,
    ClassificationEnvelope,
    ClassificationError,
    Level,
    PrivateModeConfig,
    current_envelope,
    taint_target_level,
    validate_envelope,
)
from staff.redaction import redact_value

_PROCESS_OPAQUE_SECRET: bytes = secrets.token_bytes(32)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,64}$")
_MIN_MAC_KEY_BYTES = 16

_OPAQUE_ID_KEYS: frozenset[str] = frozenset(
    {"id", "run_id", "thread_id", "target_id", "repo", "item_id", "session_id", "issue", "pr", "role_id"}
)
_COMMON_META_KEYS: frozenset[str] = _OPAQUE_ID_KEYS | frozenset(
    {
        "status",
        "state",
        "kind",
        "outcome",
        "attempt",
        "epoch",
        "seq",
        "count",
        "ok",
        "degraded",
        "level",
        "schema_version",
        "surface",
        "ts",
        "created_at",
        "updated_at",
        "duration_ms",
        "code",
    }
)
SHARED_SURFACES: frozenset[str] = frozenset(
    {
        "board",
        "turnover",
        "inventory",
        "panels",
        "run_history",
        "hub_logs",
        "activity",
        "github_check",
        "alerts",
        "capsule",
    }
)
SURFACE_ALLOWLISTS: Mapping[str, frozenset[str]] = dict.fromkeys(SHARED_SURFACES, _COMMON_META_KEYS)

APPROVED_REDACTION_IMPORTERS: frozenset[str] = frozenset(
    {
        "activity.py",
        "coordination/relay_guard.py",
        "diagnostics_logs.py",
        "email_intake/content.py",
        "memory/redact.py",
        "private_mode_emit.py",
        "staff/barb_continuity.py",
        "staff/barb_task_inventory.py",
        "staff/concerns.py",
        "staff/conversation_proposals.py",
        "staff/conversations.py",
        "staff/proposal_cards.py",
        "staff/rd_barb_memory.py",
        "staff/redaction.py",
        "staff/run_events.py",
        "staff/runner_ops.py",
        "staff/runner_stream.py",
        "staff/store.py",
        "staff/thread_helpers.py",
    }
)
APPROVED_SURFACE_GATE_IMPORTERS: frozenset[str] = frozenset({"staff/private_surface_gate.py", "staff/redaction.py"})


@dataclass(frozen=True)
class SharedProjection:
    """Typed per-surface output produced by :func:`emit_shared`."""

    surface: str
    level: Level
    opaque_id: str
    metadata: Mapping[str, Any]
    content: Any | None = None
    content_mac: str | None = None
    withheld: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "surface": self.surface,
            "level": self.level.name,
            "opaque_id": self.opaque_id,
            "metadata": dict(self.metadata),
            "withheld": self.withheld,
        }
        if self.content is not None:
            out["content"] = self.content
        if self.content_mac is not None:
            out["content_mac"] = self.content_mac
        return out


def _coerce_mac_key(mac_key: bytes | str | None) -> bytes:
    if isinstance(mac_key, str):
        key_bytes = mac_key.encode("utf-8")
    elif isinstance(mac_key, bytes):
        key_bytes = mac_key
    else:
        raise ClassificationError("emit_shared: non-empty mac_key is required when include_content_mac=True")
    if len(key_bytes) < _MIN_MAC_KEY_BYTES:
        raise ClassificationError(f"emit_shared: mac_key must be at least {_MIN_MAC_KEY_BYTES} bytes")
    return key_bytes


def opaque_identifier(raw: object, *, key: bytes | None = None) -> str:
    """Return a deterministic keyed opaque identifier ``opaque:<16-hex>`` (never raw sha256)."""
    if isinstance(raw, str) and raw.startswith("opaque:") and len(raw) == 23:
        return raw
    secret = key if key is not None else _PROCESS_OPAQUE_SECRET
    digest = hmac.new(secret, str(raw).encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"opaque:{digest}"


def _compute_content_mac(payload: Any, mac_key: bytes | str | None) -> str:
    key_bytes = _coerce_mac_key(mac_key)
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"hmac-sha256:{hmac.new(key_bytes, serialized, hashlib.sha256).hexdigest()}"


def _redact_for_non_sensitive(payload: Any) -> Any:
    """Delegate PUBLIC/INTERNAL redaction to ``staff.redaction.redact_value`` (DRY)."""
    return redact_value(payload)


def _project_metadata(
    surface: str, payload: Any, *, sensitive: bool, opaque_key: bytes | None
) -> tuple[str, dict[str, Any]]:
    allowlist = SURFACE_ALLOWLISTS[surface]
    raw_map: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    raw_id = (
        raw_map.get("id")
        or raw_map.get("run_id")
        or raw_map.get("thread_id")
        or raw_map.get("target_id")
        or raw_map.get("item_id")
        or surface
    )
    op_id = opaque_identifier(raw_id, key=opaque_key)
    projected: dict[str, Any] = {}
    for k, v in raw_map.items():
        if k not in allowlist:
            continue
        if k in _OPAQUE_ID_KEYS:
            projected[k] = opaque_identifier(v, key=opaque_key) if sensitive else v
        elif isinstance(v, (bool, int)):
            projected[k] = v
        elif isinstance(v, str):
            projected[k] = (v if _SAFE_TOKEN_RE.match(v) else "withheld") if sensitive else _redact_for_non_sensitive(v)
    return op_id, projected


def _resolve_emission_level(level: Level | ClassificationEnvelope | None, *, config: PrivateModeConfig) -> Level:
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("emit_shared: config must be a PrivateModeConfig")
    if not config.enabled:
        return Level.PUBLIC
    if isinstance(level, ClassificationEnvelope):
        base = validate_envelope(level).level
    elif isinstance(level, Level):
        base = level
    elif level is None:
        active = current_envelope()
        if active is None:
            raise ClassificationError("emit_shared: explicit level or active envelope is required")
        base = active.level
    else:
        raise ClassificationError("emit_shared: level must be a Level or ClassificationEnvelope")
    return taint_target_level(base, config=config)


def emit_shared(
    surface: str,
    payload: Any,
    *,
    level: Level | ClassificationEnvelope | None = None,
    config: PrivateModeConfig = DISABLED,
    include_content_mac: bool = False,
    mac_key: bytes | str | None = None,
    sink: Callable[[Any], Any] | None = None,
) -> SharedProjection:
    """Single shared-surface emission choke point (issue #3254, PM-4)."""
    if not isinstance(surface, str) or surface not in SURFACE_ALLOWLISTS:
        raise ClassificationError("emit_shared: unsupported shared surface")
    effective_level = _resolve_emission_level(level, config=config)
    opaque_key = _coerce_mac_key(mac_key) if mac_key is not None else None
    content_mac = _compute_content_mac(payload, mac_key) if include_content_mac else None

    if effective_level is Level.SENSITIVE:
        op_id, meta = _project_metadata(surface, payload, sensitive=True, opaque_key=opaque_key)
        projection = SharedProjection(
            surface=surface,
            level=Level.SENSITIVE,
            opaque_id=op_id,
            metadata=meta,
            content=None,
            content_mac=content_mac,
            withheld=True,
        )
        if sink is not None:
            sink(projection.to_dict())
        return projection

    redacted_payload = _redact_for_non_sensitive(payload)
    op_id, meta = _project_metadata(surface, redacted_payload, sensitive=False, opaque_key=opaque_key)
    projection = SharedProjection(
        surface=surface,
        level=effective_level,
        opaque_id=op_id,
        metadata=meta,
        content=redacted_payload,
        content_mac=content_mac,
        withheld=False,
    )
    if sink is not None:
        sink(redacted_payload)
    return projection


def find_shared_sink_import_violations(root: Path) -> list[str]:
    """Return AST import-ban violations for direct shared-sink redaction/gate imports under *root*."""
    root_path = Path(root).resolve()
    violations: list[str] = []
    for path in sorted(root_path.rglob("*.py")):
        rel = path.relative_to(root_path).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod, names = node.module, {alias.name for alias in node.names}
                if mod in {"staff.redaction", "backend.staff.redaction"} and rel not in APPROVED_REDACTION_IMPORTERS:
                    violations.append(f"{rel}:{node.lineno}: unauthorized import from {mod} ({sorted(names)})")
                if (
                    mod in {"staff.private_surface_gate", "backend.staff.private_surface_gate"}
                    and "gate_text" in names
                    and rel not in APPROVED_SURFACE_GATE_IMPORTERS
                ):
                    violations.append(f"{rel}:{node.lineno}: unauthorized gate_text import from {mod}")
    return violations
