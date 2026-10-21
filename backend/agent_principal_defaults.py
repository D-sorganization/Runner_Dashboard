"""Repo-managed agent principal defaults and agent-tokens/*.token ingestion."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from security import safe_yaml_load, validate_config_path

if TYPE_CHECKING:
    from identity import IdentityManager

log = logging.getLogger("dashboard.agent_principals")

_REPO_DEFAULTS = Path(__file__).resolve().parent.parent / "config" / "agent_principals.yml"
_PROFILE_KEYS = frozenset({"roles", "scopes", "name", "kind"})


def _defaults_path() -> Path:
    override = __import__("os").environ.get("DASHBOARD_AGENT_PRINCIPALS_FILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _REPO_DEFAULTS


def _load_config() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    path = _defaults_path()
    if not path.is_file():
        return {}, {}
    allowed = [path.parent.resolve(), _REPO_DEFAULTS.parent.resolve()]
    validate_config_path(path, allowed_roots=allowed)
    data = safe_yaml_load(path, allowed_roots=allowed)
    if not data:
        return {}, {}
    profile = data.get("assistant_profile")
    profile_dict = profile if isinstance(profile, dict) else {}
    agents = data.get("agents")
    if not isinstance(agents, list):
        return profile_dict, {}
    merged: dict[str, dict[str, Any]] = {}
    for row in agents:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        agent_id = str(row["id"])
        combined: dict[str, Any] = {k: v for k, v in profile_dict.items() if k in _PROFILE_KEYS}
        combined.update({k: v for k, v in row.items() if k in _PROFILE_KEYS or k == "id"})
        merged[agent_id] = combined
    return profile_dict, merged


def _union_list(base: list[str], extra: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in [*base, *extra]:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _principal_fields(row: dict[str, Any], agent_id: str) -> dict[str, Any]:
    roles = [str(r) for r in row.get("roles") or []]
    scopes = [str(s) for s in row.get("scopes") or []]
    kind = row.get("kind")
    return {
        "id": agent_id,
        "type": "bot",
        "name": str(row.get("name") or agent_id),
        "roles": roles or ["bot"],
        "scopes": scopes,
        "assistant_kind": str(kind) if kind else None,
    }


def merge_agent_principal_defaults(manager: IdentityManager) -> None:
    """Apply config/agent_principals.yml and ingest agent-tokens/*.token files."""
    from identity import Principal, TokenRecord  # noqa: PLC0415

    _, defaults_by_id = _load_config()

    tok_dir = manager.config_dir / "agent-tokens"
    token_files: dict[str, Path] = {}
    if tok_dir.is_dir():
        validate_config_path(tok_dir, allowed_roots=[manager.config_dir.resolve()])
        for path in sorted(tok_dir.glob("*.token")):
            token_files[path.stem] = path

    for agent_id, row in defaults_by_id.items():
        fields = _principal_fields(row, agent_id)
        existing = manager.principals.get(agent_id)
        if existing is None and agent_id not in token_files:
            continue
        if existing is None:
            manager.principals[agent_id] = Principal(**fields)
            log.info("agent principal provisioned from defaults: %s", agent_id)
            continue
        manager.principals[agent_id] = existing.model_copy(
            update={
                "name": fields["name"] if fields["name"] else existing.name,
                "roles": _union_list(existing.roles, fields["roles"]),
                "scopes": _union_list(existing.scopes, fields["scopes"]),
                "assistant_kind": fields["assistant_kind"] or existing.assistant_kind,
            }
        )

    for agent_id, path in token_files.items():
        if agent_id not in manager.principals:
            row = defaults_by_id.get(agent_id, {})
            manager.principals[agent_id] = Principal(**_principal_fields(row, agent_id))
            log.info("agent principal provisioned from token file: %s", agent_id)

        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            log.warning("agent token file %s is empty; skipping", path)
            continue
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        if any(t.token_hash == token_hash for t in manager.tokens):
            continue
        manager.tokens.append(
            TokenRecord(
                token_hash=token_hash,
                principal_id=agent_id,
                created_at=time.time(),
                name=f"agent-tokens/{path.name}",
            )
        )
        log.info("ingested agent bearer from %s for principal %s", path.name, agent_id)
