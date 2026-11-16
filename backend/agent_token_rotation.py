"""Agent token auto-rotation (#3895 slice 3).

A hub-side timer re-mints a principal's bearer token once its current token
turns :data:`ROTATE_AGE_DAYS` old, or sooner if its remaining life drops below
:data:`ROTATE_REMAINING_DAYS`. The new token is written to its
``agent-tokens/<id>.token`` file atomically with mode 600, then re-ingested so
reads start succeeding with it immediately. The prior :class:`TokenRecord` is
left valid rather than revoked at rotation time, so an agent still holding the
old token in memory keeps working through :data:`GRACE_DAYS`; a later sweep
revokes it once that window has elapsed. Rotation is logged by principal and
reason, never by token value or hash.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from typing import TYPE_CHECKING

from agent_principal_defaults import agent_token_files, merge_agent_principal_defaults, principals_lock
from agent_token_health import current_token_record, token_health_report

if TYPE_CHECKING:
    from pathlib import Path

    from identity import IdentityManager

log = logging.getLogger("dashboard.agent_token_rotation")

ROTATE_AGE_DAYS = 75
ROTATE_REMAINING_DAYS = 15
GRACE_DAYS = 7
DEFAULT_EXPIRES_IN_DAYS = 90
_DAY_SECONDS = 86400.0
_RECORD_NAME_PREFIX = "agent-tokens/"


def due_rotations(manager: IdentityManager, *, now: float | None = None) -> list[str]:
    """Principal ids whose on-disk token has crossed the age or remaining-life threshold."""
    now = time.time() if now is None else now
    due: list[str] = []
    for row in token_health_report(manager, now=now):
        if not row.get("valid"):
            continue
        agent_id = str(row["principal"])
        record = current_token_record(manager, agent_id)
        if record is None:
            continue
        age_days = (now - record.created_at) / _DAY_SECONDS
        remaining_days = row.get("days_to_expiry")
        if age_days >= ROTATE_AGE_DAYS or (remaining_days is not None and remaining_days < ROTATE_REMAINING_DAYS):
            due.append(agent_id)
    return due


def _write_token_file(path: Path, raw_token: str) -> None:
    """Atomically replace ``path`` with ``raw_token``, mode 600. Never logs the value."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".tmp-{path.name}-", suffix=".token")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw_token)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def rotate_token(manager: IdentityManager, agent_id: str, *, expires_in_days: int = DEFAULT_EXPIRES_IN_DAYS) -> None:
    """Re-mint ``agent_id``'s token, write its file, and re-ingest it. The prior token is left valid."""
    path = agent_token_files(manager).get(agent_id) or manager.config_dir / "agent-tokens" / f"{agent_id}.token"
    raw_token = manager.mint_service_token(
        agent_id, name=f"{_RECORD_NAME_PREFIX}{agent_id}.token", expires_in_days=expires_in_days
    )
    _write_token_file(path, raw_token)
    with principals_lock(manager):  # the merge writes principals in place (#4256)
        merge_agent_principal_defaults(manager)
    log.info("agent token rotated for %s; prior token kept valid for a %d-day grace window", agent_id, GRACE_DAYS)


def sweep_grace_expired(manager: IdentityManager, *, now: float | None = None) -> list[str]:
    """Revoke a principal's superseded token once :data:`GRACE_DAYS` has elapsed since rotation."""
    now = time.time() if now is None else now
    by_principal: dict[str, list] = {}
    for record in manager.tokens:
        if record.name.startswith(_RECORD_NAME_PREFIX):
            by_principal.setdefault(record.principal_id, []).append(record)

    revoked: list[str] = []
    for agent_id, records in by_principal.items():
        if len(records) < 2:
            continue
        newest = max(records, key=lambda r: r.created_at)
        if now - newest.created_at < GRACE_DAYS * _DAY_SECONDS:
            continue
        for record in records:
            if record is newest:
                continue
            manager.revoke_token(record.token_hash)
            revoked.append(agent_id)
            log.info("agent token grace window elapsed; revoked superseded token for %s", agent_id)
    return revoked


def check_token_rotation(manager: IdentityManager) -> list[str]:
    """Rotate every due principal, then revoke any superseded token past its grace window.

    Returns the ids that were rotated this check.
    """
    rotated = due_rotations(manager)
    for agent_id in rotated:
        rotate_token(manager, agent_id)
    sweep_grace_expired(manager)
    return rotated
