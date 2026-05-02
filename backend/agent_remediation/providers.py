"""External provider registry and availability probing.

Extracted from agent_remediation.py (issue #361).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any


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


PROVIDERS: dict[str, AgentProvider] = {
    "jules_cli": AgentProvider(
        provider_id="jules_cli",
        label="Jules CLI",
        execution_mode="remote_session",
        dispatch_mode="dashboard_local",
        availability_probe=("jules",),
        editable=False,
        remote=True,
        notes="Best for an operator-triggered remote Jules session from the dashboard host.",
    ),
    "jules_api": AgentProvider(
        provider_id="jules_api",
        label="Jules API",
        execution_mode="remote_session",
        dispatch_mode="github_actions",
        required_env=("JULES_API_KEY",),
        editable=False,
        remote=True,
        notes="Best automation backend for GitHub Actions because the documented Jules CLI login flow is interactive.",
    ),
    "codex_cli": AgentProvider(
        provider_id="codex_cli",
        label="Codex CLI",
        execution_mode="local_exec",
        dispatch_mode="github_actions",
        availability_probe=("codex",),
        editable=True,
        notes="Uses `codex exec` for branch-local remediation on a self-hosted runner.",
    ),
    "claude_code_cli": AgentProvider(
        provider_id="claude_code_cli",
        label="Claude Code CLI",
        execution_mode="local_exec",
        dispatch_mode="github_actions",
        availability_probe=("claude",),
        editable=True,
        notes="Uses `claude -p` with auto permissions for branch-local remediation on a self-hosted runner.",
    ),
    "ollama": AgentProvider(
        provider_id="ollama",
        label="Ollama",
        execution_mode="local_analysis",
        dispatch_mode="future",
        availability_probe=("ollama",),
        editable=False,
        experimental=True,
        notes=(
            "Useful as a low-cost analyzer or triage assistant; code-edit execution should stay gated"
            " until a stronger local agent loop is selected."
        ),
    ),
    "gemini_cli": AgentProvider(
        provider_id="gemini_cli",
        label="Gemini CLI",
        execution_mode="local_exec",
        dispatch_mode="github_actions",
        availability_probe=("gemini",),
        required_env=("GOOGLE_API_KEY",),
        editable=True,
        notes="Uses `gemini` CLI for local remediation and reasoning. Setup: https://aistudio.google.com/app/apikey",
    ),
    "cline": AgentProvider(
        provider_id="cline",
        label="Cline",
        execution_mode="local_plugin",
        dispatch_mode="future",
        availability_probe=("cline",),
        editable=False,
        experimental=True,
        notes="Reserved for future plugin-driven local remediation; no stable CLI contract is assumed here yet.",
    ),
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
