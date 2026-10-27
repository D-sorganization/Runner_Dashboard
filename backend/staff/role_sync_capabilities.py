"""Per-role/host capability map for Grok Bot ↔ RD role sync (#3100, epic #3093).

Categories only — no household payloads cross hosts without an explicit opt-in.
Slice 6 (#3099) export bundles are not required here; role YAML ``role_sync`` is the config surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

_HOST_GROKBOT = "grok-bot"
_HOST_RD = "runner-dashboard"


class HostKind(str, Enum):
    GROKBOT = "grokbot"
    RUNNER_DASHBOARD = "runner_dashboard"


class CapabilityCategory(str, Enum):
    MAIL_CONNECTOR = "mail_connector"
    CALENDAR_CONNECTOR = "calendar_connector"
    DRIVE_CONNECTOR = "drive_connector"
    CREDENTIAL_VAULT = "credential_vault"
    ROUTINES = "routines"
    CHAT_QUESTION_CARDS = "chat_question_cards"
    PERSISTENT_CLI_SESSIONS = "persistent_cli_sessions"
    RUNNERS = "runners"
    STAFF_API = "staff_api"
    HOUSEHOLD_PERSONAL_DATA = "household_personal_data"


_GROKBOT_ONLY = frozenset(
    {
        CapabilityCategory.MAIL_CONNECTOR,
        CapabilityCategory.CALENDAR_CONNECTOR,
        CapabilityCategory.DRIVE_CONNECTOR,
        CapabilityCategory.CREDENTIAL_VAULT,
        CapabilityCategory.ROUTINES,
        CapabilityCategory.CHAT_QUESTION_CARDS,
    }
)
_RD_ONLY = frozenset(
    {
        CapabilityCategory.PERSISTENT_CLI_SESSIONS,
        CapabilityCategory.RUNNERS,
        CapabilityCategory.STAFF_API,
    }
)
_HOUSEHOLD = frozenset({CapabilityCategory.HOUSEHOLD_PERSONAL_DATA})


@dataclass(frozen=True)
class RoleSyncConfig:
    """Per-role host-sync settings (overlay YAML ``role_sync`` block)."""

    relay_handoff_approved: bool = False
    household_data_opt_in: bool = False
    preferred_host: HostKind = HostKind.GROKBOT


@dataclass(frozen=True)
class CapabilityDecline:
    """Visible, non-sensitive outcome when the active host cannot run a task."""

    notice: str
    category: CapabilityCategory
    role: str
    active_host: HostKind
    peer_host: HostKind | None
    relay_eligible: bool
    code: str = "capability_unavailable"


def parse_role_sync_config(data: Mapping[str, Any] | None) -> RoleSyncConfig:
    """Parse ``role_sync`` from a role YAML mapping; unknown keys are ignored."""
    if not isinstance(data, Mapping):
        return RoleSyncConfig()
    block = data.get("role_sync")
    if not isinstance(block, Mapping):
        return RoleSyncConfig()
    host_raw = str(block.get("preferred_host") or block.get("host") or "grokbot").lower()
    rd_hosts = ("rd", "runner_dashboard", "runner-dashboard")
    preferred = HostKind.RUNNER_DASHBOARD if host_raw in rd_hosts else HostKind.GROKBOT
    return RoleSyncConfig(
        relay_handoff_approved=block.get("relay_handoff_approved") is True,
        household_data_opt_in=block.get("household_data_opt_in") is True,
        preferred_host=preferred,
    )


def active_host_for_role(config: RoleSyncConfig) -> HostKind:
    """Return the configured leader host for a role (slice 8 adds global defaults)."""
    return config.preferred_host


def requires_household_opt_in(category: CapabilityCategory) -> bool:
    return category in _HOUSEHOLD


def _peer(host: HostKind) -> HostKind:
    return HostKind.RUNNER_DASHBOARD if host == HostKind.GROKBOT else HostKind.GROKBOT


def _host_label(host: HostKind) -> str:
    return _HOST_RD if host == HostKind.RUNNER_DASHBOARD else _HOST_GROKBOT


def host_has_capability(
    host: HostKind,
    category: CapabilityCategory,
    *,
    config: RoleSyncConfig | None = None,
) -> bool:
    """True when *host* may execute tasks in *category* for the given role config."""
    cfg = config or RoleSyncConfig()
    if category in _HOUSEHOLD:
        return host == HostKind.GROKBOT or (host == HostKind.RUNNER_DASHBOARD and cfg.household_data_opt_in)
    if category in _GROKBOT_ONLY:
        return host == HostKind.GROKBOT
    if category in _RD_ONLY:
        return host == HostKind.RUNNER_DASHBOARD
    return False


def decline_for_miss(
    *,
    role: str,
    category: CapabilityCategory,
    active_host: HostKind,
    config: RoleSyncConfig,
) -> CapabilityDecline:
    """Build a visible decline for a capability miss (fail closed, never silent)."""
    assert role.strip(), "role is required"
    peer = _peer(active_host)
    household_on_rd = active_host == HostKind.RUNNER_DASHBOARD and not config.household_data_opt_in
    if requires_household_opt_in(category) and household_on_rd:
        notice = (
            f"Role '{role}' cannot use household personal data on {_host_label(active_host)} "
            "without an explicit household_data_opt_in flag."
        )
        return CapabilityDecline(
            notice=notice,
            category=category,
            role=role,
            active_host=active_host,
            peer_host=None,
            relay_eligible=False,
        )
    if host_has_capability(active_host, category, config=config):
        raise ValueError("decline_for_miss called when capability is present")
    notice = (
        f"Role '{role}' cannot run {category.value.replace('_', ' ')} on {_host_label(active_host)}; "
        f"it is available on {_host_label(peer)}."
    )
    relay_ok = config.relay_handoff_approved and host_has_capability(peer, category, config=config)
    return CapabilityDecline(
        notice=notice,
        category=category,
        role=role,
        active_host=active_host,
        peer_host=peer if relay_ok else peer,
        relay_eligible=relay_ok,
    )
