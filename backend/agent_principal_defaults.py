"""Repo-managed agent principal defaults and agent-tokens/*.token ingestion."""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from overlay_contract import get_overlay
from security import safe_yaml_load, validate_config_path

if TYPE_CHECKING:
    from identity import IdentityManager

log = logging.getLogger("dashboard.agent_principals")

_REPO_DEFAULTS = Path(__file__).resolve().parent.parent / "config" / "agent_principals.yml"
_PROFILE_KEYS = frozenset({"roles", "scopes", "name", "kind"})
_AGENT_KEYS = frozenset({"inbox_webhook_url", "inbox_roles"})
# Serialises live reloads (#4256): IdentityManager has no lock of its own.
_RELOAD_LOCK = threading.Lock()


class AgentPrincipalReloadError(RuntimeError):
    """A live reload could not read its inputs; the identity manager was left unchanged."""


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
        combined.update({k: v for k, v in row.items() if k in _PROFILE_KEYS or k in _AGENT_KEYS or k == "id"})
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
    inbox_roles = [str(r) for r in row.get("inbox_roles") or [] if str(r).strip()]
    webhook = row.get("inbox_webhook_url")
    webhook_url = str(webhook).strip() if isinstance(webhook, str) and webhook.strip() else None
    return {
        "id": agent_id,
        "type": "bot",
        "name": str(row.get("name") or agent_id),
        "roles": roles or ["bot"],
        "scopes": scopes,
        "assistant_kind": str(kind) if kind else None,
        "inbox_webhook_url": webhook_url,
        "inbox_roles": inbox_roles,
    }


def _overlay_token_paths() -> dict[str, Path]:
    """Agent token file paths declared by the private overlay (optional; #3082 cat. 2).

    The overlay is the single source of per-node token-path config (LoD: this
    module only asks ``get_overlay()``, never reads overlay files itself). An
    entry whose file does not exist is skipped with a warning, matching the
    directory-glob fallback below, which already degrades gracefully when
    nothing is there yet.
    """
    overlay = get_overlay()
    if overlay is None:
        return {}
    paths: dict[str, Path] = {}
    for agent_id, raw_path in overlay.agent_token_paths.items():
        path = Path(raw_path).expanduser()
        if not path.is_file():
            log.warning("overlay token path for %s does not exist; skipping", agent_id)
            continue
        paths[agent_id] = validate_config_path(path, allowed_roots=[path.parent.resolve()])
    return paths


def configured_agent_ids() -> list[str]:
    """Principal ids declared in config/agent_principals.yml, sorted."""
    return sorted(_load_config()[1])


def agent_token_files(manager: IdentityManager) -> dict[str, Path]:
    """Existing agent token files by principal id (overlay paths win over the local glob)."""
    tok_dir = manager.config_dir / "agent-tokens"
    token_files: dict[str, Path] = {}
    if tok_dir.is_dir():
        validate_config_path(tok_dir, allowed_roots=[manager.config_dir.resolve()])
        for path in sorted(tok_dir.glob("*.token")):
            token_files[path.stem] = path
    # Overlay-declared paths take priority over the local directory glob: the
    # overlay is what category 2 moves this config behind (#3082).
    token_files.update(_overlay_token_paths())
    return token_files


def merge_agent_principal_defaults(manager: IdentityManager) -> None:
    """Apply config/agent_principals.yml and ingest agent-tokens/*.token files."""
    from identity import Principal, TokenRecord  # noqa: PLC0415

    _, defaults_by_id = _load_config()
    token_files = agent_token_files(manager)

    for agent_id, row in defaults_by_id.items():
        fields = _principal_fields(row, agent_id)
        existing = manager.principals.get(agent_id)
        if existing is None and agent_id not in token_files:
            continue
        if existing is None:
            manager.principals[agent_id] = Principal(**fields)
            log.info("agent principal provisioned from defaults: %s", agent_id)
            continue
        merged_roles: list[str]
        if "checkin-only" in fields["roles"]:
            merged_roles = ["checkin-only"]
        else:
            merged_roles = _union_list(existing.roles, fields["roles"])
            # Preserve explicit ordering from defaults when both bot and barb are present (parity role).
            if "barb" in fields["roles"] and "barb" in merged_roles and "bot" in merged_roles:
                merged_roles = ["bot", "barb", *[r for r in merged_roles if r not in ("bot", "barb")]]
        update: dict[str, Any] = {
            "name": fields["name"] if fields["name"] else existing.name,
            "roles": merged_roles,
            "scopes": _union_list(existing.scopes, fields["scopes"]),
            "assistant_kind": fields["assistant_kind"] or existing.assistant_kind,
        }
        if fields["inbox_webhook_url"]:
            update["inbox_webhook_url"] = fields["inbox_webhook_url"]
        if fields["inbox_roles"]:
            update["inbox_roles"] = _union_list(existing.inbox_roles, fields["inbox_roles"])
        manager.principals[agent_id] = existing.model_copy(update=update)

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


_PRINCIPALS_LOCK_INIT = threading.Lock()


def principals_lock(manager: IdentityManager) -> threading.RLock:
    """The lock every runtime writer of ``manager.principals`` takes (created on first use).

    Readers do not take it: a single ``principals[...]`` lookup is safe on its own.
    A read-modify-write must hold it for the whole step, or use :func:`update_principal`.
    """
    lock = manager.__dict__.get("_principals_lock")
    if lock is None:
        with _PRINCIPALS_LOCK_INIT:
            lock = manager.__dict__.setdefault("_principals_lock", threading.RLock())
    return lock


def update_principal(manager: IdentityManager, principal_id: str, update: Callable[[Any], Any]) -> Any:
    """Replace one principal atomically: ``update`` gets the live entry (or None) and returns the new one.

    Pre: ``principal_id`` is non-empty and ``update`` returns a principal with that id.
    """
    if not principal_id:
        raise ValueError("principal_id must be non-empty")
    with principals_lock(manager):
        new = update(manager.principals.get(principal_id))
        if getattr(new, "id", None) != principal_id:
            raise ValueError(f"update must return the principal for {principal_id}")
        manager.principals[principal_id] = new
        return new


RELOAD_ATTEMPTS = 3  # merges per reload when another caller keeps writing the same principals


def _changed_meanwhile(
    manager: IdentityManager,
    principal_id: str,
    principals_before: dict[str, Any],
    state_before: dict[str, dict[str, Any]],
) -> bool:
    """True when ``principal_id`` was replaced, added or edited in place since the snapshot."""
    live = manager.principals.get(principal_id)
    before = principals_before.get(principal_id)
    if live is not before:
        return True
    return before is not None and before.model_dump() != state_before[principal_id]


def reload_agent_principals(manager: IdentityManager) -> dict[str, Any]:
    """Re-run :func:`merge_agent_principal_defaults` on a live manager (#4256 ask 1).

    A new ``config/agent_principals.yml`` row plus its ``agent-tokens/*.token``
    file become live without a hub restart. The merge is the startup merge,
    unchanged: it adds principals and tokens and unions config roles and scopes into
    existing principals (a ``checkin-only`` config role replaces the role list);
    it never removes a principal or revokes a token.

    Contract:
      * Post: returns ``{"added", "updated", "tokens_ingested", "total"}`` where
        the three lists are sorted principal ids and ``total`` is the principal
        count. No token, token hash, or file content is returned or logged.
      * Post: a second call with unchanged inputs returns three empty lists and
        leaves the manager unchanged (idempotent).
      * Raises :class:`AgentPrincipalReloadError` when the merge fails for any
        reason (unreadable or malformed config or token file); nothing from the
        failed reload is applied (all or nothing).
      * Post: only this reload's own additions and changes are written to the
        manager, entry by entry. A token revoked or minted by another caller
        while the reload runs is never restored or dropped.
      * Post: a principal another caller replaced or edited after this reload's
        snapshot is never overwritten with the stale merged copy. The staleness
        check and the write run under ``principals_lock(manager)``, the lock a
        runtime writer takes (:func:`update_principal`, token rotation), so no such
        writer can land between them. A stale merge is
        redone on the fresh state, up to ``RELOAD_ATTEMPTS`` times; if it still
        cannot settle, :class:`AgentPrincipalReloadError` is raised and nothing
        is applied. Code that writes ``manager.principals`` without that lock
        is outside this guarantee.
    """
    with _RELOAD_LOCK:
        for _attempt in range(RELOAD_ATTEMPTS):
            principals_before = dict(manager.principals)
            state_before = {pid: p.model_dump() for pid, p in principals_before.items()}
            known_hashes = {t.token_hash for t in manager.tokens}
            scratch = copy.copy(manager)
            scratch.principals = dict(principals_before)
            scratch.tokens = list(manager.tokens)
            try:
                merge_agent_principal_defaults(scratch)
            except Exception as exc:
                # Boundary catch: the merge ran on a scratch copy, so discarding it is the rollback.
                log.warning("agent principals reload failed (%s); identity store unchanged", type(exc).__name__)
                raise AgentPrincipalReloadError(type(exc).__name__) from None
            changed = {pid: p for pid, p in scratch.principals.items() if principals_before.get(pid) != p}
            new_tokens = [t for t in scratch.tokens if t.token_hash not in known_hashes]
            # Check and write under the manager's writer lock, so no other writer can land between them.
            with principals_lock(manager):
                if any(_changed_meanwhile(manager, pid, principals_before, state_before) for pid in changed):
                    # Another caller wrote one of these principals after the snapshot: the merged copy
                    # is stale, so writing it would drop that caller's change. Merge again.
                    continue
                manager.principals.update(changed)
                live_hashes = {t.token_hash for t in manager.tokens}
                manager.tokens.extend(t for t in new_tokens if t.token_hash not in live_hashes)
            break
        else:
            log.warning("agent principals reload gave up after %d contended attempts", RELOAD_ATTEMPTS)
            raise AgentPrincipalReloadError("ConcurrentUpdate")
        added = sorted(set(changed) - set(principals_before))
        updated = sorted(set(changed) & set(principals_before))
        tokens_ingested = sorted({t.principal_id for t in new_tokens})
        total = len(manager.principals)
    log.info(
        "agent principals reloaded: added=%s updated=%s tokens_ingested=%s total=%d",
        added,
        updated,
        tokens_ingested,
        total,
    )
    return {"added": added, "updated": updated, "tokens_ingested": tokens_ingested, "total": total}
