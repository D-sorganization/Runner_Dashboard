"""RD-canonical staff roles → versioned Grok Bot import bundles (#3099)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from staff.role_spec import RoleSpec, read_and_parse_role_file

FORBIDDEN_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "private_context",
        "private_overlay",
        "context_payload",
        "encrypted_context",
        "household",
        "household_data",
        "mail_archive",
        "finance_ledger",
        "personal_drive",
    }
)
_FORBIDDEN_SCOPE_KEY_RE = re.compile(
    r"^(private_|household_|mail_|finance_|personal_drive|encrypted_)",
    re.IGNORECASE,
)
_FORBIDDEN_VALUE_MARKERS: tuple[str, ...] = (
    "private_context",
    "household_data",
    "mail_archive",
    "finance_ledger",
    "personal_drive",
)


def scrub_role_export_payload(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise TypeError("data must be a mapping")
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in FORBIDDEN_TOP_LEVEL_KEYS:
            continue
        if key == "scope" and isinstance(value, dict):
            out[key] = {k: v for k, v in value.items() if not _FORBIDDEN_SCOPE_KEY_RE.match(str(k))}
            continue
        out[key] = value
    return out


def assert_bundle_export_safe(payload: dict[str, Any]) -> None:
    violations: list[str] = []
    for key in payload:
        if key in FORBIDDEN_TOP_LEVEL_KEYS:
            violations.append(f"forbidden top-level key: {key}")
    scope = payload.get("scope")
    if isinstance(scope, dict):
        for key in scope:
            if _FORBIDDEN_SCOPE_KEY_RE.match(str(key)):
                violations.append(f"forbidden scope key: {key}")
    blob = str(payload).lower()
    for marker in _FORBIDDEN_VALUE_MARKERS:
        if marker in blob:
            violations.append(f"forbidden marker in payload: {marker}")
    if violations:
        raise ValueError("; ".join(violations))


BUNDLE_FORMAT = "grok-role-bundle/v1"
_MANIFEST_NAME = "manifest.json"
_ROLES_SUBDIR = "roles"


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON (deterministic across key order)."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class RoleBundle:
    role: str
    source_file: str
    source_content_hash: str
    payload: dict[str, Any]
    payload_hash: str


def _policy_refs(spec: RoleSpec) -> dict[str, Any]:
    return {
        "playbook": spec.playbook,
        "prompt_template": spec.prompt_template,
        "holds": list(spec.holds),
        "reports_to": spec.reports_to,
    }


def _export_payload(spec: RoleSpec, raw: dict[str, Any]) -> dict[str, Any]:
    scrub_role_export_payload(raw)
    payload: dict[str, Any] = {
        "bundle_format": BUNDLE_FORMAT,
        "role": spec.name,
        "title": spec.title,
        "summary": spec.summary,
        "instructions": spec.instructions,
        "persona": spec.persona,
        "tools": list(spec.tools),
        "chat": dict(spec.chat),
        "policy_refs": _policy_refs(spec),
        "surface": spec.surface,
    }
    assert_bundle_export_safe(payload)
    return payload


def build_role_bundle(path: Path, *, raw_override: dict[str, Any] | None = None) -> RoleBundle:
    """Build one bundle from a role YAML file (pre: path is readable)."""
    if not path.is_file():
        raise FileNotFoundError(path)
    raw_bytes = path.read_bytes()
    source_hash = _sha256_bytes(raw_bytes)
    if raw_override is not None:
        raw: dict[str, Any] = raw_override
        spec = read_and_parse_role_file(path)
    else:
        spec = read_and_parse_role_file(path)
        loaded = yaml.safe_load(raw_bytes.decode("utf-8"))
        raw = loaded if isinstance(loaded, dict) else {}
    payload = _export_payload(spec, raw)
    return RoleBundle(
        role=spec.name,
        source_file=path.name,
        source_content_hash=source_hash,
        payload=payload,
        payload_hash=canonical_payload_hash(payload),
    )


def build_role_manifest(bundles: list[RoleBundle]) -> dict[str, Any]:
    """Aggregate manifest for a set of role bundles."""
    roles: dict[str, Any] = {}
    for bundle in sorted(bundles, key=lambda b: b.role):
        roles[bundle.role] = {
            "source_file": bundle.source_file,
            "source_content_hash": bundle.source_content_hash,
            "payload_hash": bundle.payload_hash,
            "payload_path": f"{_ROLES_SUBDIR}/{bundle.role}.json",
        }
    return {"bundle_format": BUNDLE_FORMAT, "roles": roles, "grok_import": {}}


def export_role_bundles(roles_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Write manifest and per-role JSON payloads under ``out_dir``."""
    if not roles_dir.is_dir():
        raise NotADirectoryError(roles_dir)
    paths = sorted(roles_dir.glob("*.yml")) + sorted(roles_dir.glob("*.yaml"))
    bundles: list[RoleBundle] = []
    for path in paths:
        spec = read_and_parse_role_file(path)
        if not spec.valid:
            continue
        bundles.append(build_role_bundle(path))
    manifest = build_role_manifest(bundles)
    out_roles = out_dir / _ROLES_SUBDIR
    out_roles.mkdir(parents=True, exist_ok=True)
    for bundle in bundles:
        rel = out_roles / f"{bundle.role}.json"
        rel.write_text(
            json.dumps(bundle.payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    (out_dir / _MANIFEST_NAME).write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def check_bundle_tree_drift(roles_dir: Path, bundle_root: Path) -> list[str]:
    if not bundle_root.is_dir():
        return [f"missing bundle root: {bundle_root}"]
    manifest = json.loads((bundle_root / _MANIFEST_NAME).read_text(encoding="utf-8"))
    if manifest.get("bundle_format") != BUNDLE_FORMAT:
        return ["unsupported bundle_format"]
    stale: list[str] = []
    for role, entry in (manifest.get("roles") or {}).items():
        role_path = roles_dir / str(entry["source_file"])
        if not role_path.is_file():
            stale.append(str(role))
            continue
        live = build_role_bundle(role_path)
        if live.source_content_hash != entry["source_content_hash"] or live.payload_hash != entry["payload_hash"]:
            stale.append(str(role))
    return stale


def apply_grok_import_report(manifest: dict[str, Any], role: str, *, payload_hash: str) -> dict[str, Any]:
    """Record Grok Bot import acknowledgement for drift detection."""
    if role not in (manifest.get("roles") or {}):
        raise KeyError(role)
    updated = dict(manifest)
    grok_import = dict(updated.get("grok_import") or {})
    grok_import[role] = {"reported_payload_hash": payload_hash}
    updated["grok_import"] = grok_import
    return updated


def grok_import_drift(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Roles whose Grok-reported hash differs from the exported payload hash."""
    rows: list[dict[str, str]] = []
    roles = manifest.get("roles") or {}
    reported = manifest.get("grok_import") or {}
    for role, entry in roles.items():
        rep = reported.get(role) or {}
        expected = str(entry.get("payload_hash") or "")
        got = str(rep.get("reported_payload_hash") or "")
        if got and got != expected:
            rows.append({"role": str(role), "expected": expected, "reported": got})
    return rows
