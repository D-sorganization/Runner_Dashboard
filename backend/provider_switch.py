"""Node switch that turns providers off on every dispatch path (#1597).

``STAFF_DISABLED_PROVIDERS`` is a comma-separated list of provider ids set in the
node's env file, e.g. ``STAFF_DISABLED_PROVIDERS=gemini``. Staff ids (``gemini``,
``claude``), dashboard ids (``gemini_cli``, ``claude_code_cli``) and Conductor
ids (``gemini-cli``) all name the same provider, so any spelling works.

Pure and dependency-free: staff chat, staff runs, the budget gate, retries and
the agent-remediation registry probes all consult it.
"""

from __future__ import annotations

import os

ENV_VAR = "STAFF_DISABLED_PROVIDERS"
DISABLED_DETAIL = f"disabled on this node ({ENV_VAR})"

# Dashboard ids whose staff id is not simply the id without ``_cli``.
_ALIASES = {"claude_code": "claude"}


def canonical_id(provider_id: str) -> str:
    """Staff-style id for any spelling: ``Gemini-CLI`` -> ``gemini``, ``claude_code_cli`` -> ``claude``."""
    pid = provider_id.strip().lower().replace("-", "_")
    pid = pid.removesuffix("_cli")
    return _ALIASES.get(pid, pid)


def disabled_providers() -> frozenset[str]:
    """Canonical ids listed in ``STAFF_DISABLED_PROVIDERS`` (empty when unset)."""
    raw = os.environ.get(ENV_VAR, "")
    return frozenset(canonical_id(part) for part in raw.split(",") if part.strip())


def is_disabled(provider_id: str) -> bool:
    """True when ``provider_id`` (any spelling) is switched off on this node."""
    return bool(provider_id.strip()) and canonical_id(provider_id) in disabled_providers()


__all__ = ["DISABLED_DETAIL", "ENV_VAR", "canonical_id", "disabled_providers", "is_disabled"]
