"""Default-off configuration, read only by the hub integration boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from staff.review_arm.policy import DEFAULT_REPOS, HELD_PRS, Candidate


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    dry_run: bool = True
    interval_seconds: int = 600
    repos: tuple[str, ...] = DEFAULT_REPOS
    held_prs: frozenset[tuple[str, int]] = HELD_PRS

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, machine: str) -> Settings:
        requested = env.get("RD_REVIEW_ARM_ENABLED", "").casefold() in {"1", "true", "yes"}
        dry_run = env.get("RD_REVIEW_ARM_DRY_RUN", "true").casefold() in {"1", "true", "yes"}
        interval = int(env.get("RD_REVIEW_ARM_INTERVAL_MINUTES", "10"))
        if not 1 <= interval <= 1440:
            raise ValueError("review-arm interval must be 1..1440 minutes")
        repos = tuple(
            s.strip() for s in env.get("RD_REVIEW_ARM_REPOS", ",".join(DEFAULT_REPOS)).split(",") if s.strip()
        )
        if not repos or len(set(repos)) != len(repos):
            raise ValueError("review-arm repos must be nonempty and unique")
        for repo in repos:
            Candidate(repo, 1, "0" * 40)
        # Seeds cannot be removed by configuration. Additional holds use repo#PR.
        held = set(HELD_PRS)
        for entry in env.get("RD_REVIEW_ARM_HELD_PRS", "").split(","):
            if not entry.strip():
                continue
            repo, number = entry.strip().rsplit("#", 1)
            Candidate(repo, int(number), "0" * 40)
            held.add((repo, int(number)))
        return cls(requested and machine.casefold() == "controltower", dry_run, interval * 60, repos, frozenset(held))
