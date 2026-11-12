"""Required status contexts for deploy/release CI gates (policy source of truth)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "config" / "required_status_checks_policy.json"


class RequiredContextsPolicyError(RuntimeError):
    """Required status-check policy could not be loaded or parsed."""


def _context_names(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, dict):
        raise RequiredContextsPolicyError("policy root must be a JSON object")
    items = raw.get("required_contexts")
    if not isinstance(items, list):
        raise RequiredContextsPolicyError("required_contexts must be a list")
    names: list[str] = []
    for item in items:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict) and item.get("context"):
            names.append(str(item["context"]))
    names = tuple(dict.fromkeys(names))
    if not names:
        raise RequiredContextsPolicyError("policy defines no required contexts")
    return names


@lru_cache(maxsize=4)
def load_required_deploy_contexts(policy_path: str | None = None) -> tuple[str, ...]:
    """Return required branch-protection contexts from the repo policy file."""
    path = Path(policy_path) if policy_path else _DEFAULT_POLICY
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RequiredContextsPolicyError(f"cannot read policy at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RequiredContextsPolicyError(f"invalid JSON in policy at {path}: {exc}") from exc
    return _context_names(raw)
