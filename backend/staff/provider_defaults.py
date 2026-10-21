"""Repo-managed default models for staff CLI providers (issue #2022).

Runs pass these explicitly on the CLI so behavior does not depend on each
host's ``~/.codex/config.toml``. ``RunBody.model`` / role model still override.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.staff.provider_defaults")

_DEFAULTS_PATH = Path(__file__).resolve().parents[2] / "config" / "staff_provider_defaults.json"


@lru_cache(maxsize=1)
def _load_defaults_file() -> dict[str, Any]:
    path = _DEFAULTS_PATH
    if not path.exists():
        log.warning("Staff provider defaults file missing at %s", path)
        return {}
    allowed_roots = {
        path.parent.resolve(),
        Path(__file__).resolve().parent.parent.resolve(),
    }
    resolved = path.resolve()
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise ValueError(f"Provider defaults path escapes allowed roots: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("staff_provider_defaults.json must be a JSON object")
    return data


def default_model_for_provider(provider_id: str) -> str | None:
    """Return the repo default model for ``provider_id``, or ``None`` when unset.

    Pre: ``provider_id`` is non-empty.
    """
    assert provider_id.strip(), "provider_id must be non-empty"  # noqa: S101
    models = _load_defaults_file().get("default_models")
    if not isinstance(models, dict):
        return None
    value = models.get(provider_id)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clear_provider_defaults_cache() -> None:
    """Test helper: drop the cached defaults file."""
    _load_defaults_file.cache_clear()
