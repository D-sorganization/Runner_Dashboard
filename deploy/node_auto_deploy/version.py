"""Deployed revision detection from deployment.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_deployment_json(path: Path) -> dict[str, Any]:
    """Load deployment metadata; return {} when missing or invalid."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def deployed_git_sha(deployment_file: Path) -> str | None:
    """Return the 40-char git SHA from deployment.json, or None."""
    payload = read_deployment_json(deployment_file)
    sha = str(payload.get("git_sha") or "").strip().lower()
    if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
        return sha
    return None
