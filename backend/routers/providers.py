"""Shared provider registry endpoint — ONE source of truth (issue #810, #812).

``GET /api/providers/registry`` returns a single, versioned provider contract
consumed by *both* the dashboard UI and the Conductor orchestrator. It replaces
the three duplicate provider lists that previously drifted (dashboard
``PROVIDERS``, the Conductor ``ProviderMeta`` set, and the ad-hoc
``_PROVIDERS_WITH_MODEL_SELECTION`` set), and it bridges the underscore (dashboard)
vs hyphen (conductor) id mismatch by carrying *both* ids on every entry.

Design principles enforced here:

- **DRY** — every static field is read from the single canonical table
  :data:`agent_remediation.PROVIDER_REGISTRY`. Nothing is re-listed here.
- **Law of Demeter** — the Ollama live-models fetch is injected
  (``ollama_models_fetcher``) so the pure assembly logic in
  :func:`build_registry` never reaches into the network layer.
- **Design by Contract** — :func:`_login_status_for` has a postcondition that
  its result is always one of the allowed literals; the assembled payload is
  validated before return.
- **Orthogonality / resilience** — a dead Ollama server yields ``models: []``
  and a reachability-reflecting ``login_status``; the endpoint never 500s.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from typing import Any

import httpx
from agent_remediation import (
    PROVIDER_REGISTRY,
    ProviderEntry,
)
from agent_remediation import probe_provider_availability as probe_legacy_availability
from agent_remediation.provider_probe import NodeAvailability, probe_provider_availability
from cache_utils import cache_get, cache_set
from conductor_constants import AUTH_KINDS, CAPABILITIES, TASK_CLASSES
from fastapi import APIRouter

log = logging.getLogger("dashboard.providers")

router = APIRouter(prefix="/api", tags=["providers"])

#: Versioned registry contract. Bump on any breaking change to the shape.
#: #1193 added ``enabled`` per provider plus top-level ``hostname`` and
#: ``node_availability`` — additive, so the version is unchanged.
REGISTRY_SCHEMA_VERSION = "1.0.0"

#: Per-node CLI probe cache (#1193): ``shutil.which`` + env/file checks per
#: request would be cheap but not free; the frontend polls this endpoint.
NODE_AVAILABILITY_CACHE_KEY = "providers:node_availability"
NODE_AVAILABILITY_TTL_SECONDS = 60

#: The four allowed login_status literals (DbC). Any other value is a bug.
_LOGIN_STATUS_LITERALS: frozenset[str] = frozenset({"authenticated", "unauthenticated", "error", "unknown"})

#: Type of the injectable Ollama models fetcher (Law of Demeter seam).
OllamaModelsFetcher = Callable[[str], list[str]]


def fetch_ollama_models(base_url: str) -> list[str]:
    """Fetch live model names from the local Ollama server.

    GET ``{base_url}/api/tags`` returns ``{"models": [{"name": ...}, ...]}``.

    Args:
        base_url: Ollama tags endpoint URL.

    Returns:
        Sorted list of model name strings.

    Raises:
        Exception: Any connection/HTTP/parse error is propagated so the caller
            (:func:`build_registry`) can degrade to ``models: []``. This keeps
            the fetcher a pure I/O seam with no swallowed errors of its own.
    """
    resp = httpx.get(base_url, timeout=2.0)
    resp.raise_for_status()
    payload = resp.json()
    models = payload.get("models", []) if isinstance(payload, dict) else []
    names = [m["name"] for m in models if isinstance(m, dict) and m.get("name")]
    return sorted(names)


def _login_status_for(
    entry: ProviderEntry,
    *,
    available: bool,
    ollama_reachable: bool | None,
) -> tuple[str, str]:
    """Derive ``(login_status, login_detail)`` from existing probes (DbC).

    Reuses the dashboard availability probe (binary + required-env presence)
    rather than reimplementing auth. For the local Ollama provider, login
    status reflects server reachability instead of credentials.

    Postcondition (asserted): the returned status is one of
    :data:`_LOGIN_STATUS_LITERALS`.
    """
    if entry.resource == "local" and entry.auth_mode == "local":
        # Local inference: "login" == server reachable.
        if ollama_reachable is None:
            status, detail = "unknown", "Local provider; reachability not probed."
        elif ollama_reachable:
            status, detail = "authenticated", "Local server reachable; no login required."
        else:
            status, detail = (
                "unauthenticated",
                "Local server unreachable; start it (e.g. `ollama serve`).",
            )
    elif entry.auth_mode == "none":
        status, detail = "authenticated", "No authentication required."
    elif available:
        status, detail = "authenticated", "Probe reports ready."
    else:
        # Distinguish missing credentials from a probe that could not run.
        missing_env = [name for name in entry.required_env if not os.environ.get(name)]
        if missing_env:
            status = "unauthenticated"
            detail = "Missing required environment: " + ", ".join(missing_env)
        else:
            status = "unauthenticated"
            detail = entry.setup_hint or "Not authenticated."

    assert status in _LOGIN_STATUS_LITERALS, f"illegal login_status {status!r}"
    return status, detail


def _provider_payload(
    entry: ProviderEntry,
    *,
    available: bool,
    models: list[str],
    ollama_reachable: bool | None,
) -> dict[str, Any]:
    """Project one canonical entry onto the registry wire contract (flat)."""
    login_status, login_detail = _login_status_for(entry, available=available, ollama_reachable=ollama_reachable)
    return {
        "id": entry.conductor_id,
        "dashboard_id": entry.dashboard_id,
        "label": entry.label,
        "execution_mode": entry.execution_mode,
        "dispatch_mode": entry.dispatch_mode,
        "auth_mode": entry.auth_mode,
        "resource": entry.resource,
        "capabilities": list(entry.capabilities),
        "cost_per_task": float(entry.cost_per_task),
        "max_concurrency": int(entry.max_concurrency),
        "models": list(models),
        "models_endpoint": entry.models_endpoint,
        "login_status": login_status,
        "login_detail": login_detail,
        "setup_hint": entry.setup_hint,
        "experimental": entry.experimental,
        "editable": entry.editable,
        "remote": entry.remote,
        "enabled": entry.enabled,
    }


def cached_node_availability() -> dict[str, NodeAvailability]:
    """Return the per-node CLI probe, cached for :data:`NODE_AVAILABILITY_TTL_SECONDS`.

    The probe itself never raises (see :mod:`agent_remediation.provider_probe`),
    so a cache miss simply recomputes and stores.
    """
    cached = cache_get(NODE_AVAILABILITY_CACHE_KEY, NODE_AVAILABILITY_TTL_SECONDS)
    if isinstance(cached, dict):
        return cached
    fresh = probe_provider_availability()
    cache_set(NODE_AVAILABILITY_CACHE_KEY, fresh)
    return fresh


def build_registry(
    *,
    ollama_models_fetcher: OllamaModelsFetcher | None = None,
    env: dict[str, str] | None = None,
    node_availability: dict[str, NodeAvailability] | None = None,
) -> dict[str, Any]:
    """Assemble the full registry payload (pure, injectable, never raises).

    Args:
        ollama_models_fetcher: Optional injected fetcher (Law of Demeter). When
            ``None``, the live :func:`fetch_ollama_models` is used. Any error it
            raises is caught here and degraded to ``models: []`` so the endpoint
            never 500s (orthogonality / resilience).
        env: Optional environment override for availability probing (testing).
        node_availability: Optional pre-computed per-node CLI probe (#1193).
            ``None`` runs the probe uncached; the HTTP endpoint passes the
            60 s-cached result so it stays fast under frontend polling.

    Returns:
        The registry dict matching the versioned contract.
    """
    fetcher = ollama_models_fetcher or fetch_ollama_models
    availability = probe_legacy_availability(env=env)
    node = probe_provider_availability() if node_availability is None else node_availability

    providers: list[dict[str, Any]] = []
    for entry in PROVIDER_REGISTRY:
        avail = availability.get(entry.dashboard_id)
        is_available = bool(avail and avail.available)

        models = list(entry.models)
        ollama_reachable: bool | None = None
        if entry.models_endpoint:
            try:
                models = fetcher(entry.models_endpoint)
                ollama_reachable = True
            except Exception as exc:  # noqa: BLE001 — resilience: never 500.
                log.info(
                    "Live models fetch failed for %s (%s); degrading to []",
                    entry.dashboard_id,
                    exc.__class__.__name__,
                )
                models = []
                ollama_reachable = False

        providers.append(
            _provider_payload(
                entry,
                available=is_available,
                models=models,
                ollama_reachable=ollama_reachable,
            )
        )

    # Postcondition: every login_status is a legal literal (DbC).
    assert all(p["login_status"] in _LOGIN_STATUS_LITERALS for p in providers)

    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "providers": providers,
        "auth_kinds": list(AUTH_KINDS),
        "task_classes": list(TASK_CLASSES),
        "capabilities": list(CAPABILITIES),
        "hostname": socket.gethostname(),
        "node_availability": node,
    }


@router.get("/providers/registry")
async def get_provider_registry() -> dict[str, Any]:
    """Return the shared provider registry (dashboard + Conductor contract)."""
    return build_registry(node_availability=cached_node_availability())
