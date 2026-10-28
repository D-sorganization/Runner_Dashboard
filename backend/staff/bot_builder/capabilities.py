"""Capability id vocabulary shared with the role-sync manifest contract (#3100 interface)."""

from __future__ import annotations

# Grok-Bot-oriented categories (subscription CLI connectors).
GROK_CAPABILITIES: frozenset[str] = frozenset(
    {
        "grok.mail_connector",
        "grok.calendar_connector",
        "grok.drive_connector",
        "grok.credential_vault",
        "grok.routines",
        "grok.chat_cards",
    },
)

# RD-oriented categories.
RD_CAPABILITIES: frozenset[str] = frozenset(
    {
        "rd.cli_sessions",
        "rd.runners",
        "rd.staff_api",
    },
)

HOUSEHOLD_CAPABILITIES: frozenset[str] = frozenset(
    {
        "household.mail",
        "household.finance",
        "household.files",
        "household.drive",
    },
)

KNOWN_CAPABILITIES: frozenset[str] = GROK_CAPABILITIES | RD_CAPABILITIES | HOUSEHOLD_CAPABILITIES
