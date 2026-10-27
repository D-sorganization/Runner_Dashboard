"""Detect whether runner-dashboard runs under system or user systemd."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

ServiceScope = Literal["system", "user"]


@dataclass(frozen=True)
class ServiceScopeDecision:
    scope: ServiceScope
    unit: str
    reason: str

    def restart_argv(self) -> list[str]:
        if self.scope == "user":
            return ["systemctl", "--user", "restart", self.unit]
        return ["sudo", "systemctl", "restart", self.unit]

    def stop_argv(self) -> list[str]:
        if self.scope == "user":
            return ["systemctl", "--user", "stop", self.unit]
        return ["sudo", "systemctl", "stop", self.unit]

    def is_active_argv(self) -> list[str]:
        if self.scope == "user":
            return ["systemctl", "--user", "is-active", self.unit]
        return ["systemctl", "is-active", self.unit]


def detect_service_scope(
    probe_results: Sequence[tuple[str, int]],
    *,
    unit: str = "runner-dashboard",
) -> ServiceScopeDecision | None:
    """Classify scope from fakeable probe tuples ``(scope_label, exit_code)``.

    ``probe_results`` entries are tried in order; the first scope with exit 0 wins.
    System scope is preferred when both are active (hub-a WSL hub pattern).
    """
    for label, code in probe_results:
        if code != 0:
            continue
        if label == "system":
            return ServiceScopeDecision("system", unit, "system unit is active")
        if label == "user":
            return ServiceScopeDecision("user", unit, "user unit is active")
    return None
