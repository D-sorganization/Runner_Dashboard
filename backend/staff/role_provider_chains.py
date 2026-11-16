"""Dieter-approved preferred provider + ordered fallback chains per staff role (2026-11-13).

Published in ``config/staff_role_provider_chains.json``. Applied when a loaded role YAML
has no explicit ``retry.failover_chain`` (Repository_Management copies may lag RD).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from staff.provider_failover_config import FailoverStep

log = logging.getLogger("dashboard.staff.role_provider_chains")

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "staff_role_provider_chains.json"

# Lane prefixes → staff provider ids (code-reviewer author skip, #4251).
_BRANCH_PREFIX_TO_PROVIDER: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^claude[-/]", re.I), "claude"),
    (re.compile(r"^codex[-/]", re.I), "codex"),
    (re.compile(r"^cursor/", re.I), "cursor-agent"),
    (re.compile(r"^gemini[-/]", re.I), "antigravity"),
    (re.compile(r"^agy[-/]", re.I), "antigravity"),
)


def _load_raw() -> dict[str, Any]:
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("staff_role_provider_chains.json unreadable (%s); enrichment disabled", exc)
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    return _load_raw()


def reset_role_provider_chains_cache() -> None:
    _config.cache_clear()


def unchanged_roles() -> frozenset[str]:
    raw = _config().get("unchanged_roles") or []
    return frozenset(str(r).strip().lower() for r in raw if str(r).strip())


def provider_default_model(provider: str) -> str | None:
    models = _config().get("provider_models") or {}
    if not isinstance(models, dict):
        return None
    raw = models.get(provider)
    if raw in (None, "", "default"):
        return None
    return str(raw).strip() or None


def approved_chain_for_role(role_name: str) -> tuple[str, ...] | None:
    """Ordered provider ids for ``role_name``, or None when policy does not apply."""
    norm = (role_name or "").strip().lower()
    if not norm or norm in unchanged_roles():
        return None
    roles = _config().get("roles") or {}
    if not isinstance(roles, dict):
        return None
    raw = roles.get(norm)
    if not isinstance(raw, list) or not raw:
        return None
    return tuple(str(p).strip() for p in raw if str(p).strip())


def failover_steps_for_providers(providers: tuple[str, ...]) -> tuple[FailoverStep, ...]:
    return tuple(FailoverStep(p, provider_default_model(p)) for p in providers)


def antigravity_restricted_repo_bases() -> frozenset[str]:
    raw = _config().get("antigravity_restricted_repo_bases") or []
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(r).strip() for r in raw if str(r).strip())


def is_antigravity_restricted_repo(repo: str) -> bool:
    """True when Gemini/antigravity must not be selected for ``repo``."""
    base = (repo or "").strip().rsplit("/", 1)[-1]
    if not base:
        return False
    if base in antigravity_restricted_repo_bases():
        return True
    return "sbir" in base.lower()


def filter_chain_for_repo(chain: tuple[FailoverStep, ...], repo: str) -> tuple[FailoverStep, ...]:
    if not chain or not is_antigravity_restricted_repo(repo):
        return chain
    return tuple(s for s in chain if s.provider != "antigravity")


def infer_author_provider_from_branch(branch: str) -> str | None:
    """Best-effort author provider from lane branch prefix."""
    ref = (branch or "").strip()
    if not ref:
        return None
    for pattern, provider in _BRANCH_PREFIX_TO_PROVIDER:
        if pattern.search(ref):
            return provider
    return None


def _has_explicit_failover_chain(data: dict[str, Any]) -> bool:
    retry = data.get("retry")
    if isinstance(retry, dict) and retry.get("failover_chain"):
        return True
    return bool(data.get("failover_chain"))


_LEGACY_RM_PROVIDER_TRIPLE = frozenset({"codex", "cursor-agent", "claude"})
_LEGACY_ANTIGRAVITY_TRIPLE = frozenset({"claude", "codex", "antigravity"})
_POLICY_ENRICHABLE_PROVIDERS = frozenset({"claude", "codex", "cursor-agent", "antigravity"})


def _yaml_awaits_policy_enrichment(data: dict[str, Any], role_name: str) -> bool:
    """True when RM-style YAML has providers but no explicit failover chain yet."""
    raw = data.get("providers") or []
    if not isinstance(raw, list):
        return False
    provs = frozenset(str(p).strip().lower() for p in raw if str(p).strip())
    if not provs:
        return False
    if not provs.issubset(_POLICY_ENRICHABLE_PROVIDERS):
        return False
    if provs == _LEGACY_RM_PROVIDER_TRIPLE:
        return True
    if provs == _LEGACY_ANTIGRAVITY_TRIPLE:
        return True
    if role_name == "librarian" and provs == frozenset({"claude"}):
        return True
    return False


def enrich_role_provider_policy(data: dict[str, Any]) -> dict[str, Any]:
    """Copy-on-write: apply approved chains when the YAML has no explicit failover_chain."""
    if not isinstance(data, dict):
        return data
    name = str(data.get("name") or "").strip().lower()
    chain = approved_chain_for_role(name)
    if chain is None:
        return data
    if _has_explicit_failover_chain(data):
        return data
    if not _yaml_awaits_policy_enrichment(data, name):
        return data
    out = dict(data)
    out["providers"] = list(chain)
    steps = [
        {"provider": s.provider, **({"model": s.model} if s.model else {})} for s in failover_steps_for_providers(chain)
    ]
    retry = dict(out["retry"]) if isinstance(out.get("retry"), dict) else {}
    retry.setdefault("max_attempts", 3)
    retry["failover_chain"] = steps
    retry["fallback_providers"] = list(chain[1:])
    out["retry"] = retry
    return out


def all_implemented_chains_table() -> list[tuple[str, str]]:
    """Role name and comma-separated chain for reports and tests."""
    roles = _config().get("roles") or {}
    if not isinstance(roles, dict):
        return []
    rows = [(str(k), ", ".join(str(p) for p in v)) for k, v in sorted(roles.items()) if isinstance(v, list)]
    for name in sorted(unchanged_roles()):
        rows.append((name, "(unchanged in policy)"))
    return rows
