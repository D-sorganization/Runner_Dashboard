"""Per-node CLI availability probe for the provider registry (issue #1193).

The canonical table (:mod:`agent_remediation.provider_registry`) says *what*
each provider needs on a node (``availability_probe`` binaries). This module
answers *whether this dashboard host has it*: for every provider that declares
an ``availability_probe`` it reports

- ``installed`` — ``shutil.which`` finds the first probe binary on this host,
- ``authenticated`` — derived from the existing credential probes in
  :mod:`routers.credentials` (reused, never re-implemented), or, for
  ``auth_mode == "local"`` CLIs without a registered probe, from ``installed``
  (the CLI carries its own login session),
- ``detail`` — one human-readable line.

Design notes:

- **DRY** — the credentials router already knows how to tell whether Codex,
  Claude, Gemini, Ollama and Jules are usable; those functions are imported
  and called, not copied.
- **Law of Demeter / testability** — ``credential_probes`` is injectable and
  the default mapping is resolved lazily so importing this module never pulls
  the FastAPI router graph in at import time (and never creates an import
  cycle: ``routers.*`` imports ``agent_remediation``, not the other way round
  at module load).
- **Resilience** — a probe that raises is reported as ``authenticated: False``
  with the exception class in ``detail``; the function never raises.
"""

from __future__ import annotations

import inspect
import logging
import shutil
from collections.abc import Callable, Mapping
from typing import Any

from agent_remediation.provider_registry import PROVIDER_REGISTRY, ProviderEntry

log = logging.getLogger("dashboard")

#: A credentials-router probe: no arguments, returns the flat probe dict.
CredentialProbe = Callable[[], dict[str, Any]]

#: Wire shape of one node-availability row.
NodeAvailability = dict[str, Any]


def _default_credential_probes() -> dict[str, CredentialProbe]:
    """Map dashboard credential ids to the synchronous probes in ``routers.credentials``.

    Resolved lazily (inside the call) so a monkeypatched router attribute is
    honoured and the router graph is not imported at module load. Async probes
    (e.g. Cline's VS Code extension probe) are excluded: this function runs in
    the request path and must stay cheap and synchronous.
    """
    from routers import credentials  # noqa: PLC0415 — lazy on purpose (see module docstring)

    candidates: dict[str, CredentialProbe] = {
        "jules_cli": credentials._probe_jules_cli,  # noqa: SLF001
        "jules_api": credentials._probe_jules_api,  # noqa: SLF001
        "codex_cli": credentials._probe_codex_cli,  # noqa: SLF001
        "claude_code_cli": credentials._probe_claude_code_cli,  # noqa: SLF001
        "gemini_cli": credentials._probe_gemini_cli,  # noqa: SLF001
        "ollama": credentials._probe_ollama,  # noqa: SLF001
    }
    return {k: v for k, v in candidates.items() if not inspect.iscoroutinefunction(v)}


def _probe_one(
    entry: ProviderEntry,
    *,
    installed: bool,
    binary: str,
    credential_probe: CredentialProbe | None,
) -> NodeAvailability:
    """Build one row (pure given its inputs; never raises)."""
    if credential_probe is not None:
        try:
            probe = credential_probe()
        except Exception as exc:  # noqa: BLE001 — resilience: never 500 the registry.
            log.warning("Credential probe for %s failed: %s", entry.dashboard_id, type(exc).__name__)
            return {
                "installed": installed,
                "authenticated": False,
                "detail": f"credential probe failed: {type(exc).__name__}",
            }
        return {
            "installed": installed,
            "authenticated": bool(probe.get("authenticated")),
            "detail": str(probe.get("detail") or ""),
        }

    if not installed:
        return {"installed": False, "authenticated": False, "detail": f"{binary} not found on PATH"}
    if entry.auth_mode == "local":
        return {
            "installed": True,
            "authenticated": True,
            "detail": f"{binary} found on PATH; CLI manages its own login session (no credential probe)",
        }
    return {
        "installed": True,
        "authenticated": False,
        "detail": f"{binary} found on PATH; no credential probe registered for auth_mode {entry.auth_mode}",
    }


def probe_provider_availability(
    entries: tuple[ProviderEntry, ...] = PROVIDER_REGISTRY,
    *,
    credential_probes: Mapping[str, CredentialProbe] | None = None,
) -> dict[str, NodeAvailability]:
    """Report ``{dashboard_id: {installed, authenticated, detail}}`` for this node.

    Only providers that declare an ``availability_probe`` are included; a
    provider reached over HTTP (Maxwell) has no binary to look for here.

    Args:
        entries: Registry rows to probe (defaults to the canonical table).
        credential_probes: Optional override of the credential-probe mapping
            keyed by ``ProviderEntry.effective_credential_id``. ``None`` uses
            the probes registered in ``routers.credentials``; ``{}`` disables
            credential reuse (tests).

    Returns:
        A flat dict; every row has exactly the three keys above and never
        raises (a failing probe is reported, not propagated).
    """
    probes = _default_credential_probes() if credential_probes is None else credential_probes
    result: dict[str, NodeAvailability] = {}
    for entry in entries:
        if not entry.availability_probe:
            continue
        binary = entry.availability_probe[0]
        installed = shutil.which(binary) is not None
        result[entry.dashboard_id] = _probe_one(
            entry,
            installed=installed,
            binary=binary,
            credential_probe=probes.get(entry.effective_credential_id),
        )
    return result


__all__ = ["CredentialProbe", "NodeAvailability", "probe_provider_availability"]
