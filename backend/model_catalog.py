"""Live per-provider model catalog with daily refresh and last-good cache (#3533)."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_remediation.provider_registry import _ANTIGRAVITY_MODELS, _CURSOR_AGENT_MODELS

log = logging.getLogger("dashboard.model_catalog")

DAILY_REFRESH_INTERVAL_SECONDS = 86_400.0
CATALOG_PROVIDERS: tuple[str, ...] = ("codex", "claude", "cursor-agent", "antigravity", "gemini", "grok")

_PROVIDER_ALIASES: dict[str, str] = {
    "codex": "codex",
    "codex_cli": "codex",
    "codex-cli": "codex",
    "claude": "claude",
    "claude_code_cli": "claude",
    "claude-cli": "claude",
    "claude-code": "claude",
    "cursor-agent": "cursor-agent",
    "cursor_agent": "cursor-agent",
    "antigravity": "antigravity",
    "antigravity-cli": "antigravity",
    "agy": "antigravity",
    "gemini": "gemini",
    "gemini_cli": "gemini",
    "gemini-cli": "gemini",
    "grok": "grok",
}

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/][A-Za-z0-9._:/-]{0,79}$")
_IGNORED_TOKENS = frozenset(
    {
        "id",
        "model",
        "models",
        "name",
        "provider",
        "default",
        "available",
        "description",
        "status",
        "version",
        "tier",
        "none",
        "true",
        "false",
    }
)

DEFAULT_SEED_CATALOG: dict[str, tuple[str, ...]] = {
    "codex": ("gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-astra"),
    "claude": ("claude-opus-5-5", "claude-fable-5-1", "opus", "sonnet", "haiku"),
    "cursor-agent": _CURSOR_AGENT_MODELS,
    "antigravity": _ANTIGRAVITY_MODELS,
    "gemini": ("gemini-3.8-flash", "gemini-3.8-flash-high", "gemini-2.5-pro", "gemini-2.5-flash"),
    "grok": tuple(m for m in _CURSOR_AGENT_MODELS if "grok" in m),
}

_CLI_PROBES: dict[str, tuple[str, ...]] = {
    "codex": ("codex", "--models"),
    "claude": ("claude", "--models"),
    "cursor-agent": ("cursor-agent", "--models"),
    "antigravity": ("agy", "--models"),
    "gemini": ("gemini", "--models"),
    "grok": ("cursor-agent", "--models"),
}

ProviderModelFetcher = Callable[[str], Sequence[str]]


def normalize_catalog_provider(provider_id: str) -> str | None:
    """Map dashboard, conductor, or staff provider IDs onto canonical catalog keys."""
    return _PROVIDER_ALIASES.get((provider_id or "").strip().lower())


def _valid_model_id(token: str) -> bool:
    if not token or token.lower() in _IGNORED_TOKENS or not _MODEL_ID_RE.fullmatch(token):
        return False
    return any(ch.isalpha() for ch in token) and (
        any(sep in token for sep in ("-", ":", ".")) or token in {"opus", "sonnet", "haiku", "auto"}
    )


def parse_provider_models(provider_id: str, raw_output: str) -> list[str]:
    """Parse CLI model listing or JSON models response into deduplicated model IDs."""
    canonical = normalize_catalog_provider(provider_id) or provider_id.strip().lower()
    text = (raw_output or "").strip()
    if not text:
        return []

    candidates: list[str] = []
    if text[0] in ("{", "["):
        try:
            parsed = json.loads(text)
            items = parsed if isinstance(parsed, list) else (parsed.get("models") or parsed.get("data") or [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, str):
                        candidates.append(item.strip())
                    elif isinstance(item, dict):
                        val = item.get("id") or item.get("name") or item.get("model")
                        if isinstance(val, str):
                            candidates.append(val.strip())
        except json.JSONDecodeError:
            pass

    if not candidates:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "=", "-")):
                candidates.append(stripped.lstrip("*•- ").split()[0].strip(",:;()[]\"'"))

    out: list[str] = []
    for token in candidates:
        if _valid_model_id(token) and (canonical != "grok" or "grok" in token.lower()) and token not in out:
            out.append(token)
    return out


def probe_cli_models(provider_id: str, *, runner: Callable[[Sequence[str]], str] | None = None) -> list[str]:
    """Probe a provider's CLI for its live model list."""
    canonical = normalize_catalog_provider(provider_id)
    if canonical is None:
        raise ValueError(f"Unsupported catalog provider: {provider_id!r}")
    argv = _CLI_PROBES[canonical]
    if runner is not None:
        models = parse_provider_models(canonical, runner(argv))
        if not models:
            raise RuntimeError(f"Empty model list from {canonical} probe")
        return models
    exe = shutil.which(argv[0])
    if exe:
        try:
            proc = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=3.0, check=True)
            models = parse_provider_models(canonical, proc.stdout)
            if models:
                return models
        except Exception as exc:  # noqa: BLE001
            log.debug("CLI model probe for %s fell back to seed (%s)", canonical, exc)
    return list(DEFAULT_SEED_CATALOG.get(canonical, ()))


@dataclass
class ModelCatalogState:
    providers: dict[str, list[str]] = field(default_factory=dict)
    refreshed_at: float | None = None
    stale: bool = False
    last_error: dict[str, str] = field(default_factory=dict)
    cache_path: Path | None = None


_STATE = ModelCatalogState(providers={k: list(v) for k, v in DEFAULT_SEED_CATALOG.items()}, refreshed_at=time.time())


def reset_model_catalog(*, seed: bool = True, now: float | None = None, cache_path: Path | None = None) -> None:
    """Reset the in-memory catalog state (used by tests)."""
    ts = time.time() if now is None else float(now)
    _STATE.providers = (
        {k: list(v) for k, v in DEFAULT_SEED_CATALOG.items()} if seed else {k: [] for k in CATALOG_PROVIDERS}
    )
    _STATE.refreshed_at = ts if seed else None
    _STATE.stale = False
    _STATE.last_error = {}
    _STATE.cache_path = cache_path


def refresh_catalog(*, fetcher: ProviderModelFetcher | None = None, now: float | None = None) -> dict[str, Any]:
    """Refresh all catalog providers; preserve last good cache and age on failure."""
    ts = time.time() if now is None else float(now)
    probe_fn = fetcher or (lambda pid: probe_cli_models(pid))
    any_error = any_success = False

    for pid in CATALOG_PROVIDERS:
        try:
            models = [m for m in probe_fn(pid) if _valid_model_id(str(m))]
            if not models:
                raise RuntimeError(f"No models returned for {pid}")
            _STATE.providers[pid] = list(dict.fromkeys(models))
            _STATE.last_error.pop(pid, None)
            any_success = True
        except Exception as exc:  # noqa: BLE001
            any_error = True
            _STATE.last_error[pid] = str(exc)
            _STATE.providers.setdefault(pid, [])
            log.warning("Model catalog refresh failed for %s (%s); keeping last good cache", pid, exc)

    if any_success and not any_error:
        _STATE.refreshed_at = ts
        _STATE.stale = False
    elif any_success and _STATE.refreshed_at is None:
        _STATE.refreshed_at = ts
        _STATE.stale = True
    else:
        _STATE.stale = True

    if _STATE.cache_path is not None and any_success:
        try:
            _STATE.cache_path.parent.mkdir(parents=True, exist_ok=True)
            _STATE.cache_path.write_text(
                json.dumps({"providers": _STATE.providers, "refreshed_at": _STATE.refreshed_at}),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Failed to persist model catalog cache: %s", exc)

    return get_catalog(now=ts, auto_refresh=False)


def get_catalog(
    *, now: float | None = None, auto_refresh: bool = True, fetcher: ProviderModelFetcher | None = None
) -> dict[str, Any]:
    """Return the current catalog snapshot, auto-refreshing when older than 24h."""
    ts = time.time() if now is None else float(now)
    if (
        auto_refresh
        and _STATE.refreshed_at is not None
        and (ts - _STATE.refreshed_at) >= DAILY_REFRESH_INTERVAL_SECONDS
    ):
        return refresh_catalog(fetcher=fetcher, now=ts)
    age = round(max(0.0, ts - _STATE.refreshed_at), 3) if _STATE.refreshed_at is not None else None
    return {
        "providers": {k: list(v) for k, v in _STATE.providers.items()},
        "refreshed_at": _STATE.refreshed_at,
        "age_seconds": age,
        "stale": _STATE.stale,
        "last_error": dict(_STATE.last_error),
        "refresh_interval_seconds": int(DAILY_REFRESH_INTERVAL_SECONDS),
    }


def catalog_models_for_provider(provider_id: str) -> list[str]:
    """Return cached catalog models for a dashboard, conductor, or staff provider ID."""
    canonical = normalize_catalog_provider(provider_id)
    return list(_STATE.providers.get(canonical, ())) if canonical else []
