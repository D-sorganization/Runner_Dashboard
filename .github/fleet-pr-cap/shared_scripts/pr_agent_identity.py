"""Pull-request identity parsing kept separate from lease roster semantics."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Final

PR_AGENT_ALIASES: Final[dict[str, str]] = {
    "cursor-agent": "cursor",
    "cursor": "cursor",
    "agy": "antigravity",
}


def normalize_pr_agent_id(agent_id: str) -> str:
    """Normalize a public PR alias while leaving lease IDs intact elsewhere."""
    return PR_AGENT_ALIASES.get(agent_id, agent_id)


def resolve_pr_agent(
    pr: Mapping[str, object],
    *,
    agent_ids: Sequence[str],
    agent_label_prefix: str,
    identify_agent: Callable[[str, bool], str],
) -> str | None:
    """Resolve agent label, branch prefix, then configured bot login."""
    agent, ambiguous_labels = _agent_from_labels(pr, agent_ids, agent_label_prefix)
    if ambiguous_labels:
        return None
    if agent is not None:
        return agent

    agent = _agent_from_branch(pr_head_branch(pr), agent_ids)
    if agent is not None:
        return agent

    login, is_bot = pr_author(pr)
    if not isinstance(login, str):
        return None

    identity = identify_agent(login, bool(is_bot))
    if identity in {"user", "local"}:
        return None
    return _normalize_exact(identity, agent_ids)


def _agent_from_labels(
    pr: Mapping[str, object],
    agent_ids: Sequence[str],
    label_prefix: str,
) -> tuple[str | None, bool]:
    """Resolve labels, rejecting unknown or conflicting agent identities."""
    labels = pr.get("labels")
    if not isinstance(labels, (list, tuple)):
        return None, False
    identities: set[str] = set()
    unknown_identity = False
    for label in labels:
        name = label.get("name") if isinstance(label, Mapping) else label
        if not isinstance(name, str) or not name.startswith(label_prefix):
            continue
        candidate = name[len(label_prefix) :]
        identity = _normalize_exact(candidate, agent_ids)
        if identity is None:
            unknown_identity = True
        else:
            identities.add(identity)
    if unknown_identity:
        return None, True
    if len(identities) == 1:
        return next(iter(identities)), False
    return None, len(identities) > 1


def pr_head_branch(pr: Mapping[str, object]) -> str:
    """Return the head branch from GitHub REST or CLI PR metadata."""
    for key in ("headRefName", "head_ref", "head_branch", "branch"):
        value = pr.get(key)
        if isinstance(value, str):
            return value
    head = pr.get("head")
    if isinstance(head, Mapping):
        ref = head.get("ref")
        if isinstance(ref, str):
            return ref
    return ""


def pr_repository(pr: Mapping[str, object]) -> str | None:
    """Return the PR target repository from CLI or REST metadata.

    A fork's ``head.repo`` is deliberately ignored: policy applies to the
    repository receiving the pull request.
    """
    repository = pr.get("repository")
    if isinstance(repository, str):
        return repository.strip() or None
    if isinstance(repository, Mapping):
        for key in ("full_name", "nameWithOwner"):
            value = repository.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    base = pr.get("base")
    if isinstance(base, Mapping):
        target = base.get("repo")
        if isinstance(target, Mapping):
            value = target.get("full_name")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def pr_author(pr: Mapping[str, object]) -> tuple[str | None, bool]:
    """Return author login and bot status from REST or CLI PR metadata."""
    author = pr.get("author")
    user = pr.get("user")
    person = author if isinstance(author, Mapping) else user
    login = pr.get("author_login")
    is_bot: object = pr.get("is_bot", pr.get("isBot"))
    author_type: object = None
    if isinstance(person, Mapping):
        login = person.get("login", login)
        is_bot = person.get("is_bot", person.get("isBot", is_bot))
        author_type = person.get("type")
    if not isinstance(login, str):
        return None, False
    if isinstance(is_bot, bool):
        return login, is_bot
    if isinstance(author_type, str):
        return login, author_type.casefold() == "bot"
    return login, login.casefold().endswith("[bot]")


def _agent_from_branch(branch: str, agent_ids: Sequence[str]) -> str | None:
    """Resolve conventional branch prefixes against roster IDs and aliases."""
    if branch.startswith("orch/"):
        match = re.match(r"^orch/\d+-(?P<agent>[a-z0-9-]+)(?:/|$)", branch)
        if match is None:
            return None
        candidate = match.group("agent")
        return _normalize_exact(candidate, agent_ids)
    elif branch.startswith("staff/"):
        candidate = branch[len("staff/") :].split("/", 1)[0]
        return _normalize_staff_role(candidate, agent_ids)
    elif "/" in branch:
        candidate = branch.split("/", 1)[0]
    else:
        return None
    return _normalize_exact(candidate, agent_ids)


def _normalize_exact(candidate: str, agent_ids: Sequence[str]) -> str | None:
    """Normalize only exact roster IDs and declared PR aliases."""
    if candidate in agent_ids or candidate in PR_AGENT_ALIASES:
        return normalize_pr_agent_id(candidate)
    return None


def _normalize_staff_role(candidate: str, agent_ids: Sequence[str]) -> str | None:
    """Allow documented role suffixes, preferring the longest exact identity."""
    exact = _normalize_exact(candidate, agent_ids)
    if exact is not None:
        return exact
    identities = set(agent_ids).union(PR_AGENT_ALIASES)
    matches = [
        identity for identity in identities if candidate.startswith(f"{identity}-")
    ]
    if not matches:
        return None
    return normalize_pr_agent_id(max(matches, key=len))
