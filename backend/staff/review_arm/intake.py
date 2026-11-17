"""Budgeted intake: one list per repo, confirmations only for candidates.

The read adapter owns GitHub header backoff (raise Backoff), required-check
confirmation at head, complete hold data, and exact-head run deduplication.
Incomplete/paginated label or run data must be reported as unavailable, never
as an empty list. The coordinator stops the cycle on a rate-limit refusal.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Protocol

from staff.review_arm.policy import DEFAULT_REPOS, HELD_PRS, Candidate, decide


class Backoff(OSError):
    """GitHub primary/secondary rate limit; no more calls this cycle."""


class IntakeSource(Protocol):
    def list_open(self, repo: str) -> Iterable[Candidate]: ...
    def confirm(self, candidate: Candidate) -> Candidate: ...


class Intake:
    """Read-only adapter for the coordinator; no dispatch or arm capability."""

    def __init__(
        self,
        source: IntakeSource,
        *,
        repos: tuple[str, ...] = DEFAULT_REPOS,
        held_prs: frozenset[tuple[str, int]] = HELD_PRS,
    ) -> None:
        if not repos or len(set(repos)) != len(repos):
            raise ValueError("repos must be nonempty and unique")
        for repo in repos:
            Candidate(repo, 1, "0" * 40)
        self._source = source
        self._repos = repos
        self._held_prs = held_prs

    def candidates(self) -> Iterable[Candidate]:
        for repo in self._repos:
            for candidate in self._source.list_open(repo):
                if candidate.repo != repo:
                    raise ValueError("list source returned a candidate from another repository")
                preliminary = decide(
                    replace(candidate, checks_green=True, checks_head=candidate.head_sha), self._held_prs
                )
                if preliminary.action == "skip":
                    yield candidate
                    continue
                try:
                    confirmed = self._source.confirm(candidate)
                    if (confirmed.repo, confirmed.pr) != (candidate.repo, candidate.pr):
                        raise ValueError("confirmation target mismatch")
                    if confirmed.head_sha != candidate.head_sha:
                        yield replace(candidate, checks_green=False)
                    else:
                        yield confirmed
                except Backoff:
                    raise
                except Exception:  # noqa: BLE001 — missing holds/check evidence fails closed.
                    yield replace(candidate, holds_available=False)
