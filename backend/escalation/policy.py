"""Trusted-actor policy for the priority:expedite label."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from escalation.constants import TRUSTED_LOGINS_CONFIG

_REPO_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def trusted_github_logins() -> frozenset[str]:
    """Load trusted GitHub actor logins from the shared fleet config file."""
    path = Path(os.environ.get("EXPEDITE_TRUSTED_LOGINS_FILE", str(_REPO_ROOT / TRUSTED_LOGINS_CONFIG))).expanduser()
    raw = json.loads(path.read_text(encoding="utf-8"))
    logins = raw.get("logins") if isinstance(raw, dict) else raw
    if not isinstance(logins, list) or not logins:
        raise ValueError(f"{path}: expected non-empty logins list")
    normalized = frozenset(str(x).strip().lower() for x in logins if str(x).strip())
    if not normalized:
        raise ValueError(f"{path}: no valid logins")
    return normalized


def is_trusted_github_actor(login: str) -> bool:
    """Pre: login is the GitHub sender login from a label event."""
    if not login or not login.strip():
        return False
    return login.strip().lower() in trusted_github_logins()
