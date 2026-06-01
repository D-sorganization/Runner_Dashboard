"""External provider registry and availability probing.

Extracted from agent_remediation.py (issue #361).

DRY refactor (issue #810): :data:`PROVIDERS` is no longer a hand-maintained
second copy of provider identity/metadata. It is *generated* from the single
canonical table in :mod:`agent_remediation.provider_registry`
(``PROVIDER_REGISTRY``). To change a provider, edit that table — this module
derives the legacy :class:`AgentProvider` shape from it for back-compat.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any

from agent_remediation.provider_registry import PROVIDER_REGISTRY, ProviderEntry


@dataclass(frozen=True, slots=True)
class AgentProvider:
    provider_id: str
    label: str
    execution_mode: str
    dispatch_mode: str
    availability_probe: tuple[str, ...] = field(default_factory=tuple)
    required_env: tuple[str, ...] = field(default_factory=tuple)
    editable: bool = False
    remote: bool = False
    experimental: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    provider_id: str
    available: bool
    status: str
    detail: str
    binary_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _agent_provider_from_entry(entry: ProviderEntry) -> AgentProvider:
    """Project a canonical :class:`ProviderEntry` onto the legacy shape.

    The dashboard's historical ``availability_probe`` for the codex/claude/
    gemini/cline providers probed the bare binary name, which is exactly the
    first segment of the conductor id without the ``-cli`` suffix for some
    providers. Rather than re-derive heuristically, the canonical table carries
    ``availability_probe`` explicitly; we copy it through.
    """
    return AgentProvider(
        provider_id=entry.dashboard_id,
        label=entry.label,
        execution_mode=entry.execution_mode,
        dispatch_mode=entry.dispatch_mode,
        availability_probe=entry.availability_probe,
        required_env=entry.required_env,
        editable=entry.editable,
        remote=entry.remote,
        experimental=entry.experimental,
        notes=entry.notes,
    )


#: Generated from the single canonical table (DRY, issue #810). Do not edit by
#: hand — edit ``agent_remediation.provider_registry.PROVIDER_REGISTRY``.
PROVIDERS: dict[str, AgentProvider] = {
    entry.dashboard_id: _agent_provider_from_entry(entry) for entry in PROVIDER_REGISTRY
}


def probe_provider_availability(
    env: dict[str, str] | None = None,
) -> dict[str, ProviderAvailability]:
    env_map = env or os.environ
    availability: dict[str, ProviderAvailability] = {}
    for provider_id, provider in PROVIDERS.items():
        if provider.required_env:
            missing = [name for name in provider.required_env if not env_map.get(name)]
            if missing:
                availability[provider_id] = ProviderAvailability(
                    provider_id=provider_id,
                    available=False,
                    status="missing_env",
                    detail="Missing required environment: " + ", ".join(missing),
                )
                continue
        binary_path = None
        if provider.availability_probe:
            binary_path = shutil.which(provider.availability_probe[0])
            if not binary_path:
                availability[provider_id] = ProviderAvailability(
                    provider_id=provider_id,
                    available=False,
                    status="missing_binary",
                    detail=f"{provider.availability_probe[0]} not found on PATH",
                )
                continue
        availability[provider_id] = ProviderAvailability(
            provider_id=provider_id,
            available=True,
            status="available",
            detail="ready",
            binary_path=binary_path,
        )
    return availability
