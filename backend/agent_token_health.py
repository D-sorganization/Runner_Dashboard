"""Agent bearer token health: presence, validity and time to expiry (#3895 slice 1).

For each principal in ``config/agent_principals.yml`` the report says whether its
token file exists, whether the identity store accepts it for that principal, and
how long until it expires. The token value and its hash never leave this module.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

from agent_principal_defaults import agent_token_files, configured_agent_ids

if TYPE_CHECKING:
    from identity import IdentityManager

_DAY_SECONDS = 86400.0


def _expires_at(manager: IdentityManager, raw: str) -> float | None:
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return next((t.expires_at for t in manager.tokens if t.token_hash == token_hash), None)


def _check(manager: IdentityManager, agent_id: str, raw: str) -> tuple[bool, str | None]:
    principal, reason = manager.resolve_bearer_token(raw)
    if principal is None:
        return False, reason
    if principal.id != agent_id:
        return False, "principal_mismatch"
    return True, None


def token_health_report(manager: IdentityManager, now: float | None = None) -> list[dict[str, Any]]:
    """One row per configured agent principal, sorted by id.

    Row keys: ``principal``, ``present``, ``valid``, ``reason`` (None when valid),
    ``expires_at`` (epoch seconds or None), ``seconds_to_expiry`` and
    ``days_to_expiry`` (None without a known expiry; negative once expired).
    """
    now = time.time() if now is None else now
    files = agent_token_files(manager)
    report: list[dict[str, Any]] = []
    for agent_id in configured_agent_ids():
        row: dict[str, Any] = {
            "principal": agent_id,
            "present": False,
            "valid": False,
            "reason": "token_file_missing",
            "expires_at": None,
            "seconds_to_expiry": None,
            "days_to_expiry": None,
        }
        path = files.get(agent_id)
        if path is not None and path.is_file():
            raw = path.read_text(encoding="utf-8").strip()
            row["present"] = True
            if not raw:
                row["reason"] = "token_file_empty"
            else:
                row["valid"], row["reason"] = _check(manager, agent_id, raw)
                expires_at = _expires_at(manager, raw)
                if expires_at is not None:
                    remaining = expires_at - now
                    row.update(
                        expires_at=expires_at,
                        seconds_to_expiry=remaining,
                        days_to_expiry=remaining / _DAY_SECONDS,
                    )
        report.append(row)
    return report


def render_token_health_lines(report: list[dict[str, Any]]) -> list[str]:
    """Prometheus 0.0.4 lines for a token health report (labels: ``principal``)."""
    from routers.metrics import sanitize_metric_label  # noqa: PLC0415

    rows = [(sanitize_metric_label(row["principal"]), row) for row in report]
    lines = [
        "# HELP rd_agent_token_expiry_seconds Seconds until the agent bearer token expires (negative once expired).",
        "# TYPE rd_agent_token_expiry_seconds gauge",
    ]
    lines += [
        f'rd_agent_token_expiry_seconds{{principal="{lbl}"}} {float(row["seconds_to_expiry"]):.0f}'
        for lbl, row in rows
        if row.get("seconds_to_expiry") is not None
    ]
    for key, help_text in (
        ("present", "Whether the agent token file exists (1) or is missing (0)."),
        ("valid", "Whether the identity store accepts the agent token for its principal (1) or not (0)."),
    ):
        lines += [f"# HELP rd_agent_token_{key} {help_text}", f"# TYPE rd_agent_token_{key} gauge"]
        lines += [f'rd_agent_token_{key}{{principal="{lbl}"}} {int(bool(row.get(key)))}' for lbl, row in rows]
    return lines
