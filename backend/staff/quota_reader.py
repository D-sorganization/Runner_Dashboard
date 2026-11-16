"""Per-provider quota read visibility (#4278 J-12).

Claude and Codex expose local plan-window readers via :mod:`staff.quota`.
Every other staff provider has no on-node quota source; those rows must report
``unknown``, never a synthetic ``available`` or headroom pass from missing data.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from staff import quota as quota_mod

QuotaReadStatus = Literal["known", "unknown"]


def read_status(
    provider: str,
    now: datetime,
    *,
    store: quota_mod.QuotaStore | None = None,
    codex_dirs: Sequence[Path] | None = None,
) -> QuotaReadStatus:
    """Whether this provider has a live quota snapshot on this node.

    Post: ``known`` only for readable providers (claude/codex) with a non-empty
    snapshot; every other provider and every missing snapshot is ``unknown``.
    """
    if provider not in quota_mod.QUOTA_ACCOUNT:
        return "unknown"
    snap = quota_mod.for_provider(provider, now, store=store, codex_dirs=codex_dirs)
    return "known" if snap is not None else "unknown"


def credential_probe_quota(
    provider: str,
    *,
    ceiling: float = 100.0,
    now: datetime | None = None,
    store: quota_mod.QuotaStore | None = None,
    codex_dirs: Sequence[Path] | None = None,
) -> tuple[QuotaReadStatus, bool | None, str]:
    """Quota row for a credential probe — never fail-open as available.

    Returns:
        ``(read_status, blocked, detail)``. ``blocked`` is ``None`` when
        ``read_status`` is ``unknown`` (install/auth must not treat that as headroom).
        When ``known``, ``blocked`` is True if the plan is over ``ceiling`` or hard-limited.
    """
    when = now or datetime.now(UTC)
    status = read_status(provider, when, store=store, codex_dirs=codex_dirs)
    if status == "unknown":
        if provider in quota_mod.QUOTA_ACCOUNT:
            return status, None, f"{provider}: no quota data yet"
        return status, None, f"{provider}: quota not readable on this node"
    ok, reason = quota_mod.headroom(provider, ceiling, now=when, store=store, codex_dirs=codex_dirs)
    return status, (not ok), reason


__all__ = ["QuotaReadStatus", "credential_probe_quota", "read_status"]
