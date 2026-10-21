"""Repo-managed agent principal defaults and agent-tokens/*.token ingestion."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from security import safe_yaml_load, validate_config_path

if TYPE_CHECKING:
    from identity import IdentityManager

log = logging.getLogger("dashboard.agent_principals")

_REPO_DEFAULTS = Path(__file__).resolve().parent.parent / "config" / "agent_principals.yml"


def _defaults_path() -> Path:
    override = __import__("os").environ.get("DASHBOARD_AGENT_PRINCIPALS_FILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _REPO_DEFAULTS


def _load_defaults() -> list[dict]:
    path = _defaults_path()
    if not path.is_file():
        return []
    allowed = [path.parent.resolve(), _REPO_DEFAULTS.parent.resolve()]
    validate_config_path(path, allowed_roots=allowed)
    data = safe_yaml_load(path, allowed_roots=allowed)
    if not data:
        return []
    agents = data.get("agents")
    return agents if isinstance(agents, list) else []


def _union_list(base: list[str], extra: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in [*base, *extra]:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def merge_agent_principal_defaults(manager: IdentityManager) -> None:
    """Apply config/agent_principals.yml and ingest agent-tokens/*.token files."""
    from identity import Principal, TokenRecord  # noqa: PLC0415

    defaults = _load_defaults()
    defaults_by_id = {str(row["id"]): row for row in defaults if isinstance(row, dict) and row.get("id")}

    tok_dir = manager.config_dir / "agent-tokens"
    token_files: dict[str, Path] = {}
    if tok_dir.is_dir():
        validate_config_path(tok_dir, allowed_roots=[manager.config_dir.resolve()])
        for path in sorted(tok_dir.glob("*.token")):
            token_files[path.stem] = path

    for agent_id, row in defaults_by_id.items():
        roles = [str(r) for r in row.get("roles") or []]
        scopes = [str(s) for s in row.get("scopes") or []]
        name = str(row.get("name") or agent_id)
        existing = manager.principals.get(agent_id)
        if existing is None and agent_id not in token_files:
            continue
        if existing is None:
            manager.principals[agent_id] = Principal(
                id=agent_id,
                type="bot",
                name=name,
                roles=roles or ["bot"],
                scopes=scopes,
            )
            log.info("agent principal provisioned from defaults: %s", agent_id)
            continue
        manager.principals[agent_id] = existing.model_copy(
            update={
                "name": name if name else existing.name,
                "roles": _union_list(existing.roles, roles),
                "scopes": _union_list(existing.scopes, scopes),
            }
        )

    for agent_id, path in token_files.items():
        if agent_id not in manager.principals:
            row = defaults_by_id.get(agent_id, {})
            manager.principals[agent_id] = Principal(
                id=agent_id,
                type="bot",
                name=str(row.get("name") or agent_id),
                roles=[str(r) for r in row.get("roles") or ["bot"]],
                scopes=[str(s) for s in row.get("scopes") or []],
            )
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
