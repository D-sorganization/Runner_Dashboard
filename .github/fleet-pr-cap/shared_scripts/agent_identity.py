"""Canonical agent identity registry.

Single source of truth for the set of agents operating across the fleet,
their priority order, WIP caps, and the GitHub author logins that identify
them. Every coordination consumer (redundant-PR closer, lease reaper,
triage workflows, dashboards) imports from here so policy changes happen in
one place.

This module has **no external dependencies** — it is imported by both
workflow scripts and local tooling, and must stay importable from a bare
Python stdlib environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from shared_scripts.pr_agent_identity import (
    normalize_pr_agent_id as _normalize_pr_agent_id,
)
from shared_scripts.pr_agent_identity import (
    pr_author as _pr_author,
)
from shared_scripts.pr_agent_identity import (
    pr_head_branch as _pr_head_branch,
)
from shared_scripts.pr_agent_identity import (
    pr_repository as _pr_repository,
)
from shared_scripts.pr_agent_identity import (
    resolve_pr_agent as _resolve_pr_agent,
)

# --------------------------------------------------------------------------- #
#  The agent roster                                                           #
# --------------------------------------------------------------------------- #

#: Stable, lowercase identifiers used in label names (``claim:<id>``,
#: ``agent:<id>``), lease comments, and the Projects v2 ``Agent`` field.
AGENT_IDS: Final[tuple[str, ...]] = (
    "user",
    "orchestrator",
    "maxwell-daemon",
    "claude",
    "codex",
    "antigravity",
    "gemini",
    "cursor-agent",
    "conductor",
    "night-watch",
    "cartographer",
    "test-hygiene",
    "sanitation",
    "pr-remediator",
    "issue-remediator",
    "jules",
    "grok-pm",
    "local",
    "gaai",
)

#: Priority order. Earlier = higher priority. Used by the redundant-PR
#: closer: when two PRs target the same issue, the one from the
#: higher-priority agent wins. ``user`` is absolute — automation never
#: closes a user PR.
PRIORITY_ORDER: Final[tuple[str, ...]] = AGENT_IDS


@dataclass(frozen=True)
class AgentProfile:
    """Static profile for one agent in the roster.

    Attributes:
        agent_id: Stable lowercase identifier (matches a ``claim:<id>`` label).
        display_name: Human-friendly name for dashboards.
        author_logins: GitHub author logins (bots and service accounts) that
            identify this agent on PRs. ``user`` is empty because humans are
            detected by ``!author.is_bot`` rather than an allowlist.
        wip_cap: Max in-flight claims fleet-wide. ``None`` means uncapped.
        is_scheduler: True for agents that dispatch other agents
            (``maxwell-daemon``, future GAAI replacements). Schedulers may
            co-label a PR with the execution backend.
        phased_out: True if no new work should be routed to this agent.
    """

    agent_id: str
    display_name: str
    author_logins: frozenset[str]
    wip_cap: int | None
    is_scheduler: bool = False
    phased_out: bool = False


PROFILES: Final[dict[str, AgentProfile]] = {
    "user": AgentProfile(
        agent_id="user",
        display_name="Human contributor",
        author_logins=frozenset(),
        wip_cap=None,
    ),
    "orchestrator": AgentProfile(
        agent_id="orchestrator",
        display_name="Grok Orchestrator (retired)",
        # Retired into Barb (#1733). The label is retained only so historical
        # claims drain; the role never authored PRs under its own login.
        author_logins=frozenset(),
        wip_cap=25,
        is_scheduler=True,
    ),
    "maxwell-daemon": AgentProfile(
        agent_id="maxwell-daemon",
        display_name="Maxwell Daemon",
        author_logins=frozenset(
            {
                "maxwell-daemon-bot",
                "maxwell-daemon[bot]",
            }
        ),
        wip_cap=30,
        is_scheduler=True,
    ),
    "claude": AgentProfile(
        agent_id="claude",
        display_name="Claude Code",
        author_logins=frozenset(
            {
                "claude-bot",
                "claude[bot]",
                "anthropic-claude[bot]",
                "d-sorgclaudeagent[bot]",
            }
        ),
        wip_cap=15,
    ),
    "codex": AgentProfile(
        agent_id="codex",
        display_name="Codex",
        author_logins=frozenset(
            {
                "codex-bot",
                "codex[bot]",
                "openai-codex[bot]",
                "d-sorgcodexagent[bot]",
            }
        ),
        wip_cap=15,
    ),
    "antigravity": AgentProfile(
        agent_id="antigravity",
        display_name="Antigravity",
        # Antigravity runs interactively under the operator's own GitHub
        # login, so it is identified locally (fleet-guard identity) rather
        # than by a bot author; listed in fleet_manifest unattended_agents.
        author_logins=frozenset(),
        wip_cap=15,
    ),
    "gemini": AgentProfile(
        agent_id="gemini",
        display_name="Gemini CLI",
        # Runner Dashboard staff runs lease under the provider id (#1706); Gemini CLI
        # runs under the operator's login like Antigravity, so no bot author.
        author_logins=frozenset(),
        wip_cap=15,
    ),
    "cursor-agent": AgentProfile(
        agent_id="cursor-agent",
        display_name="Cursor Agent CLI (Grok models)",
        # The Grok-as-worker provider on the Staff Hub (Cursor Ultra seat);
        # same tier as the other coding CLIs.
        author_logins=frozenset(),
        wip_cap=15,
    ),
    "conductor": AgentProfile(
        agent_id="conductor",
        display_name="Conductor",
        # Conductor never authors PRs under its own login: it dispatches a
        # CLI provider (claude/codex/...) whose GitHub App identity signs the
        # PR and marks it with the ``conductor`` label instead. Registering
        # it here is what lets ``build_lease("conductor")`` post issue
        # leases (unregistered agents raised ``unknown agent`` and the
        # fail-open wrapper swallowed it, so no lease was ever posted).
        author_logins=frozenset(),
        wip_cap=20,
        is_scheduler=True,
    ),
    "night-watch": AgentProfile(
        agent_id="night-watch",
        display_name="Night Watch",
        # Night Watch is a bot-optional role for overnight triage, runner
        # health, worktree/branch hygiene, and docs compliance. Any human
        # or coding agent can execute the playbook; no dedicated bot login.
        author_logins=frozenset(),
        wip_cap=10,
        is_scheduler=False,
    ),
    "cartographer": AgentProfile(
        agent_id="cartographer",
        display_name="Cartographer",
        # Cartographer maintains codebase maps for agent context. Bot-optional
        # role; any agent or human can run the inventory and refresh workflows.
        author_logins=frozenset(),
        wip_cap=10,
        is_scheduler=False,
    ),
    "test-hygiene": AgentProfile(
        agent_id="test-hygiene",
        display_name="Test Hygiene",
        # Test Hygiene hardens fleet tests: vacuous assertions, over-mocking,
        # and mutation survivors. Bot-optional role; any agent or human can
        # execute the playbook.
        author_logins=frozenset(),
        wip_cap=8,
        is_scheduler=False,
    ),
    "sanitation": AgentProfile(
        agent_id="sanitation",
        display_name="Sanitation Engineer",
        # Sanitation Engineer handles structural clutter cleanup: worktrees,
        # stale branches, orphaned PRs, temp artifacts, docs IA hygiene.
        # Bot-optional role; any agent or human can execute the playbook.
        author_logins=frozenset(),
        wip_cap=10,
        is_scheduler=False,
    ),
    "pr-remediator": AgentProfile(
        agent_id="pr-remediator",
        display_name="PR Remediator",
        # PR Remediator is a Night Watch wing that finds idle open PRs and
        # shepherds them to green squash-merge. Skips draft/do-not-automate/
        # claim:local/complex redesign. Bot-optional role.
        author_logins=frozenset(),
        wip_cap=10,
        is_scheduler=False,
    ),
    "issue-remediator": AgentProfile(
        agent_id="issue-remediator",
        display_name="Issue Remediator",
        # Issue Remediator is a Night Watch wing that tackles long-standing
        # low-medium complexity open issues overnight. Skips Board HOLDs/
        # claim:local/P0 redesign. Bot-optional role.
        author_logins=frozenset(),
        wip_cap=10,
        is_scheduler=False,
    ),
    "jules": AgentProfile(
        agent_id="jules",
        display_name="Jules",
        author_logins=frozenset(
            {
                "jules-bot",
                "jules-ai[bot]",
                "jules-bot[bot]",
            }
        ),
        wip_cap=20,
    ),
    "grok-pm": AgentProfile(
        agent_id="grok-pm",
        display_name="Grok Project Manager",
        # PM bots (PM-UpstreamDrift, PM-AffineDrift, PM-Gasification) work
        # through other agents or human direction; they do not author PRs
        # directly under their own login.
        author_logins=frozenset(),
        wip_cap=15,
    ),
    "local": AgentProfile(
        agent_id="local",
        display_name="Local human session",
        author_logins=frozenset(),
        wip_cap=None,
    ),
    "gaai": AgentProfile(
        agent_id="gaai",
        display_name="GAAI (legacy)",
        author_logins=frozenset(
            {
                "gaai-bot",
                "gaai[bot]",
            }
        ),
        wip_cap=0,
        phased_out=True,
    ),
}


# --------------------------------------------------------------------------- #
#  Labels                                                                     #
# --------------------------------------------------------------------------- #

CLAIM_LABEL_PREFIX: Final[str] = "claim:"
AGENT_LABEL_PREFIX: Final[str] = "agent:"

#: Label indicating the work must not be automated regardless of triage.
DO_NOT_AUTOMATE_LABEL: Final[str] = "do-not-automate"


def claim_label(agent_id: str) -> str:
    """Return the ``claim:<agent>`` label for an agent.

    Raises:
        ValueError: if ``agent_id`` is not in the roster.
    """
    if agent_id not in PROFILES:
        raise ValueError(f"Unknown agent id: {agent_id!r}")
    return f"{CLAIM_LABEL_PREFIX}{agent_id}"


def agent_label(agent_id: str) -> str:
    """Return the ``agent:<agent>`` label for an agent.

    Raises:
        ValueError: if ``agent_id`` is not in the roster.
    """
    if agent_id not in PROFILES:
        raise ValueError(f"Unknown agent id: {agent_id!r}")
    return f"{AGENT_LABEL_PREFIX}{agent_id}"


# --------------------------------------------------------------------------- #
#  Author-login lookup                                                        #
# --------------------------------------------------------------------------- #


def identify_agent(author_login: str, is_bot: bool) -> str:
    """Return the agent id for a PR/issue author, or ``"user"`` if human.

    Lookup rules, in order:

    1. **Known bot login wins first**, regardless of ``is_bot``. The
       ``gh`` CLI does not always populate ``author.is_bot`` (older
       versions, some JSON selectors), and fleet bot logins like
       ``jules-bot`` / ``codex-bot`` don't carry the ``[bot]`` suffix.
       If we already know the login, trust the registry.
    2. If the login isn't known and ``is_bot`` is False and the login
       is non-empty, classify as ``user``.
    3. Otherwise (unknown login, empty login, or unknown bot), classify
       as ``local``. ``local`` is the catch-all for "not a fleet agent
       we coordinate with" — dependabot, renovate, humans running
       scripts without a login, etc.
    """
    login_lc = author_login.lower()
    for profile in PROFILES.values():
        if any(login_lc == known.lower() for known in profile.author_logins):
            return profile.agent_id
    if not is_bot and author_login:
        return "user"
    return "local"


def resolve_pr_agent(pr: Mapping[str, object]) -> str | None:
    """Resolve a PR to its normalized public identity using the fleet roster."""
    return _resolve_pr_agent(
        pr,
        agent_ids=AGENT_IDS,
        agent_label_prefix=AGENT_LABEL_PREFIX,
        identify_agent=identify_agent,
    )


def normalize_pr_agent_id(agent_id: str) -> str:
    """Normalize a PR identity alias without changing lease roster ids.

    ``cursor`` is the PR identity for roster member ``cursor-agent``; ``agy``
    is the accepted PR alias for ``antigravity``.
    """
    return _normalize_pr_agent_id(agent_id)


def pr_author(pr: Mapping[str, object]) -> tuple[str | None, bool]:
    """Return author login and bot status from a GitHub PR payload."""
    return _pr_author(pr)


def pr_head_branch(pr: Mapping[str, object]) -> str:
    """Return head branch from a GitHub PR payload."""
    return _pr_head_branch(pr)


def pr_repository(pr: Mapping[str, object]) -> str | None:
    """Return target repository from a GitHub PR payload."""
    return _pr_repository(pr)


# --------------------------------------------------------------------------- #
#  Priority comparison                                                        #
# --------------------------------------------------------------------------- #


def priority_rank(agent_id: str) -> int:
    """Return the numeric priority of an agent (lower is higher priority).

    Agents not in ``PRIORITY_ORDER`` get a sentinel rank after every known
    agent so unknown authors never outrank a known one.
    """
    try:
        return PRIORITY_ORDER.index(agent_id)
    except ValueError:
        return len(PRIORITY_ORDER)


def outranks(a: str, b: str) -> bool:
    """Return True iff ``a`` has strictly higher priority than ``b``."""
    return priority_rank(a) < priority_rank(b)
