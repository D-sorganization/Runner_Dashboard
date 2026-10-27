"""Shared types and pure CI classification for staff run verification."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

GH_TIMEOUT_SECONDS = 30

_RED_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required", "startup_failure"})
_IGNORED_CONCLUSIONS = frozenset({"cancelled", "stale"})


class GitHubLookupError(RuntimeError):
    """GitHub could not answer; the run stays ``unverified``."""


@dataclass(frozen=True)
class PullRequest:
    """The PR for a run's branch. ``state``: open | merged | closed; ``ci``: green | pending | red."""

    number: int
    state: str
    ci: str


@dataclass(frozen=True)
class Verdict:
    verification: str
    detail: str
    pr_number: int | None = None
    artifact: str = ""  # typed completion evidence (#1808): pr_open | pr_merged | ""
    ci_red: bool = False  # failed because head CI is red (the run's work may still be good)


class PrProbe(Protocol):
    def find(self, repo: str, branch: str) -> PullRequest | None:
        """The newest PR whose head is ``branch`` in ``repo``, or None. Raises GitHubLookupError."""

    def get(self, repo: str, number: int) -> PullRequest:
        """PR ``number`` in ``repo`` with head CI state. Raises GitHubLookupError."""

    def remote_ahead_by(self, repo: str, branch: str, base: str = "main") -> int:
        """Commits ``branch`` is ahead of ``base`` on the remote (0 if absent). Raises GitHubLookupError."""


def ci_state(check_runs: Iterable[Mapping[str, Any]]) -> str:
    """``red`` if any head check failed, else ``pending`` until all counted checks completed."""
    counted = [r for r in check_runs if r.get("conclusion") not in _IGNORED_CONCLUSIONS]
    if any(r.get("conclusion") in _RED_CONCLUSIONS for r in counted):
        return "red"
    if not counted or any(r.get("status") != "completed" for r in counted):
        return "pending"
    return "green"
