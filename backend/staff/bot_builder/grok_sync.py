"""Sync published bot-builder versions to Grok Bot via #3099 hashed bundles (RD #3108, slice 14).

Published builder versions flow to Grok Bot equivalents exclusively through the
#3099 hashed export bundle format. The Grok Bot side is read-only for role
configuration; edits go through RD draft/validate/publish. Hash drift fails
closed with a #3064 notice without overwriting either side. Beyond the staleness
TTL, an unsynced Grok-side bot is marked disabled.
"""

from __future__ import annotations

import enum
import json
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from staff.bot_builder.versions import RoleVersion
from staff.grok_role_bundle import (
    BUNDLE_FORMAT,
    assert_bundle_export_safe,
    canonical_payload_hash,
    scrub_role_export_payload,
)

DEFAULT_GROK_SYNC_STALENESS_TTL_S: float = 1800.0
_MANIFEST_NAME = "manifest.json"
_ROLES_SUBDIR = "roles"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

NoticeSender = Callable[[str, dict[str, Any]], dict[str, Any]]


class GrokBotReadOnlyError(PermissionError):
    """Raised when Grok-side code attempts to mutate role configuration directly."""


class GrokSyncDriftError(RuntimeError):
    """Raised when a Grok Bot bundle import fails closed on hash drift or missing state."""


class GrokBotSyncState(enum.Enum):
    IN_SYNC = "in_sync"
    PENDING_SYNC = "pending_sync"
    DRIFT_BLOCKED = "drift_blocked"
    STALE_DISABLED = "stale_disabled"


@dataclass(frozen=True)
class BuilderRoleBundle:
    role: str
    version_id: str
    source_content_hash: str
    payload: dict[str, Any]
    payload_hash: str
    exported_at: float


@dataclass(frozen=True)
class GrokSyncStatus:
    role: str
    version_id: str
    state: GrokBotSyncState
    grok_bot_enabled: bool
    expected_payload_hash: str
    reported_payload_hash: str | None
    age_seconds: float
    staleness_ttl_seconds: float
    reason: str


def _check_finite_non_negative(value: float, name: str) -> float:
    val = float(value)
    if not math.isfinite(val) or val < 0.0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return val


def _require_role(role: str) -> str:
    norm = str(role or "").strip()
    if not norm:
        raise ValueError("role must be non-empty")
    return norm


def refuse_grok_role_config_edit(role: str, *, attempted_fields: Sequence[str] = ()) -> None:
    """DbC: Grok Bot side is read-only for role configuration; all edits must flow through RD."""
    role_norm = _require_role(role)
    fields = sorted(str(f) for f in attempted_fields if str(f).strip())
    note = f" ({', '.join(fields)})" if fields else ""
    raise GrokBotReadOnlyError(
        f"Grok Bot side is read-only for role {role_norm!r}{note}; "
        "all role configuration edits must go through RD bot-builder draft/validate/publish."
    )


def build_builder_version_bundle(
    version: RoleVersion,
    *,
    synced_at: float | None = None,
) -> BuilderRoleBundle:
    """Build a #3099-compatible hashed export bundle from an immutable RoleVersion."""
    if not isinstance(version, RoleVersion):
        raise TypeError("version must be a RoleVersion")
    ts = _check_finite_non_negative(time.time() if synced_at is None else synced_at, "synced_at")
    role_def = version.role
    raw_candidate: dict[str, Any] = {
        "bundle_format": BUNDLE_FORMAT,
        "role": role_def.name,
        "title": role_def.name,
        "summary": role_def.description,
        "instructions": role_def.instructions,
        "persona": role_def.persona,
        "tools": list(role_def.skills),
        "chat": {"providers": list(role_def.providers), "model": role_def.model},
        "policy_refs": {
            "holds": [],
            "schedule": role_def.schedule,
            "triggers": [{"target_role": t.target_role, "event": t.event} for t in role_def.triggers],
            "capabilities": list(role_def.capabilities),
        },
        "surface": role_def.host,
        "source_content_hash": version.content_hash,
        "grok_read_only": True,
    }
    payload = scrub_role_export_payload(raw_candidate)
    assert_bundle_export_safe(payload)
    return BuilderRoleBundle(
        role=role_def.name,
        version_id=version.version_id,
        source_content_hash=version.content_hash,
        payload=payload,
        payload_hash=canonical_payload_hash(payload),
        exported_at=ts,
    )


def _load_manifest(bundle_root: Path) -> dict[str, Any]:
    manifest_path = bundle_root / _MANIFEST_NAME
    if not manifest_path.is_file():
        return {"bundle_format": BUNDLE_FORMAT, "roles": {}, "grok_import": {}}
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("bundle_format") != BUNDLE_FORMAT:
        raise GrokSyncDriftError("invalid or unsupported bundle manifest format")
    return loaded


def _write_manifest(bundle_root: Path, manifest: dict[str, Any]) -> None:
    bundle_root.mkdir(parents=True, exist_ok=True)
    (bundle_root / _MANIFEST_NAME).write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def export_published_version_bundle(
    version: RoleVersion,
    bundle_root: Path,
    *,
    synced_at: float | None = None,
) -> BuilderRoleBundle:
    """Write a published RoleVersion into the #3099 bundle directory and update manifest.json."""
    if not isinstance(bundle_root, Path):
        raise TypeError("bundle_root must be a Path")
    bundle = build_builder_version_bundle(version, synced_at=synced_at)
    roles_dir = bundle_root / _ROLES_SUBDIR
    roles_dir.mkdir(parents=True, exist_ok=True)
    (roles_dir / f"{bundle.role}.json").write_text(
        json.dumps(bundle.payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    manifest = _load_manifest(bundle_root)
    roles = dict(manifest.get("roles") or {})
    roles[bundle.role] = {
        "source_file": f"bot_builder:{bundle.role}@{bundle.version_id}",
        "version_id": bundle.version_id,
        "source_content_hash": bundle.source_content_hash,
        "payload_hash": bundle.payload_hash,
        "payload_path": f"{_ROLES_SUBDIR}/{bundle.role}.json",
        "exported_at": bundle.exported_at,
        "grok_read_only": True,
    }
    manifest["roles"] = roles
    _write_manifest(bundle_root, manifest)
    return bundle


def export_rollback_version_bundle(
    rolled_back_version: RoleVersion,
    bundle_root: Path,
    *,
    synced_at: float | None = None,
) -> BuilderRoleBundle:
    """Re-export the restored content after a rollback_publish (#3108)."""
    if rolled_back_version.rolled_back_from is None:
        raise ValueError("export_rollback_version_bundle requires a version produced by rollback")
    return export_published_version_bundle(rolled_back_version, bundle_root, synced_at=synced_at)


def _emit_sync_notice(
    notifier: NoticeSender | None,
    *,
    role: str,
    code: str,
    summary: str,
    recommended_action: str,
    dedupe_suffix: str,
) -> None:
    if notifier is not None:
        notifier(
            "rd-barb",
            {
                "kind": "alert",
                "urgency": "high",
                "summary": summary,
                "recommended_action": recommended_action,
                "dedupe_key": f"grok-sync-{code}-{role}-{dedupe_suffix}",
                "meta": {"role": role, "code": code, "channel": "rd-barb-3064"},
            },
        )


def verify_grok_bundle_import(
    bundle_root: Path,
    *,
    role: str,
    reported_payload_hash: str,
    now: float | None = None,
    notifier: NoticeSender | None = None,
) -> dict[str, Any]:
    """Record a Grok Bot bundle import; fail closed and emit a #3064 notice on hash mismatch."""
    role_norm = _require_role(role)
    hash_norm = str(reported_payload_hash or "").strip().lower()
    if not _HEX64_RE.match(hash_norm):
        raise ValueError("reported_payload_hash must be a 64-char lowercase hex sha256")
    ts = _check_finite_non_negative(time.time() if now is None else now, "now")

    manifest = _load_manifest(bundle_root)
    roles = manifest.get("roles") or {}
    if role_norm not in roles:
        raise KeyError(f"unknown exported role: {role_norm!r}")

    expected_hash = str(roles[role_norm].get("payload_hash") or "")
    grok_import = dict(manifest.get("grok_import") or {})
    matched = hash_norm == expected_hash
    state = GrokBotSyncState.IN_SYNC if matched else GrokBotSyncState.DRIFT_BLOCKED
    grok_import[role_norm] = {"reported_payload_hash": hash_norm, "reported_at": ts, "status": state.value}
    manifest["grok_import"] = grok_import
    _write_manifest(bundle_root, manifest)

    if not matched:
        exp_short, got_short = expected_hash[:12], hash_norm[:12]
        _emit_sync_notice(
            notifier,
            role=role_norm,
            code="hash_drift",
            summary=f"Grok Bot bundle hash mismatch for role '{role_norm}': expected {exp_short}, got {got_short}",
            recommended_action=f"Re-export '{role_norm}' bundle from RD and re-import on Grok Bot.",
            dedupe_suffix=f"{exp_short}-{got_short}",
        )
        raise GrokSyncDriftError(
            f"Grok Bot bundle hash mismatch for {role_norm!r}: expected {expected_hash}, got {hash_norm}"
        )

    return {"ok": True, "role": role_norm, "payload_hash": expected_hash, "state": state.value}


def evaluate_grok_sync_status(
    bundle_root: Path,
    *,
    role: str,
    now: float | None = None,
    staleness_ttl_s: float = DEFAULT_GROK_SYNC_STALENESS_TTL_S,
    notifier: NoticeSender | None = None,
) -> GrokSyncStatus:
    """Evaluate sync health for ``role``; mark Grok-side bot disabled on drift or TTL expiry."""
    role_norm = _require_role(role)
    ts = _check_finite_non_negative(time.time() if now is None else now, "now")
    ttl = _check_finite_non_negative(staleness_ttl_s, "staleness_ttl_s")
    if ttl <= 0.0:
        raise ValueError("staleness_ttl_s must be positive")

    manifest = _load_manifest(bundle_root)
    roles = manifest.get("roles") or {}
    if role_norm not in roles:
        raise KeyError(f"unknown exported role: {role_norm!r}")

    entry = roles[role_norm]
    expected_hash = str(entry.get("payload_hash") or "")
    version_id = str(entry.get("version_id") or "")
    age = max(0.0, ts - float(entry.get("exported_at") or 0.0))

    rep = (manifest.get("grok_import") or {}).get(role_norm) or {}
    reported_hash = str(rep.get("reported_payload_hash") or "") or None
    rep_status = str(rep.get("status") or "")

    def _status(state: GrokBotSyncState, enabled: bool, reason: str) -> GrokSyncStatus:
        return GrokSyncStatus(role_norm, version_id, state, enabled, expected_hash, reported_hash, age, ttl, reason)

    if reported_hash == expected_hash and rep_status != GrokBotSyncState.DRIFT_BLOCKED.value:
        return _status(GrokBotSyncState.IN_SYNC, True, "Grok Bot bundle hash matches RD published version.")
    if rep_status == GrokBotSyncState.DRIFT_BLOCKED.value and age <= ttl:
        return _status(GrokBotSyncState.DRIFT_BLOCKED, False, "Grok Bot reported a mismatched bundle hash.")
    if age > ttl:
        _emit_sync_notice(
            notifier,
            role=role_norm,
            code="stale_ttl_disabled",
            summary=f"Grok Bot '{role_norm}' disabled: unsynced {version_id} stale ({int(age)}s > {int(ttl)}s TTL)",
            recommended_action=f"Import bundle {version_id} ({expected_hash[:12]}) on Grok Bot for '{role_norm}'.",
            dedupe_suffix=f"{version_id}-stale",
        )
        return _status(GrokBotSyncState.STALE_DISABLED, False, f"Stale unsynced bundle beyond {int(ttl)}s TTL.")
    return _status(GrokBotSyncState.PENDING_SYNC, False, "Published RD bundle is awaiting Grok Bot import.")
