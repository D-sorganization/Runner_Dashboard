"""Fleet Staff Hub — run, schedule and monitor AI coding agents ("staff").

Epic #1192. This package is the execution engine that turns a named staff
role (Night Watch, Issue Remediator, Project Steward, …) into a local CLI
subprocess on this dashboard node, records every run in a node-local SQLite
store, and streams its output to the operator console.

Boundaries (see Repository_Management/docs/sibling-repos.md):
  * Role definitions are published by Repository_Management as
    ``staff/roles/*.yml``; this package only *reads* them by path.
  * The RM lease ritual (check_agent_claim → post_agent_lease →
    agent_communicate) is invoked as a subprocess, never imported.
  * Provider CLIs (claude, codex, agy, gemini, cursor-agent, ollama) are
    spawned as subprocesses; nothing here talks to a model API directly.
"""

from staff.reply_contract import (
    ACTIONS_SCHEMA,
    ChatReply,
    ProposedAction,
    parse_reply,
    validate_action_schema,
)

__all__ = [
    "ACTIONS_SCHEMA",
    "ChatReply",
    "ProposedAction",
    "parse_reply",
    "validate_action_schema",
]
