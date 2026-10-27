"""Attention policy for green-but-unreleased main (RD #2932)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from node_auto_deploy.ci_gate import MainWindowScan

DEFAULT_MAX_UNRELEASED_HOURS = 3.0
DEFAULT_MAX_GREEN_BEHIND = 5


@dataclass(frozen=True)
class ReleaseStaleness:
    attention: bool
    reason: str = ""


def assess_release_staleness(
    scan: MainWindowScan,
    commit_times: dict[str, datetime],
    now: datetime,
    *,
    max_age_hours: float = DEFAULT_MAX_UNRELEASED_HOURS,
    max_green_behind: int = DEFAULT_MAX_GREEN_BEHIND,
) -> ReleaseStaleness:
    """Flag when gate-green main commits have gone unreleased too long or too many deep.

    Pre: ``now`` and ``commit_times`` values are timezone-aware.
    Post: attention only for green-unreleased commits; red main is a different problem.
    """
    sha = scan.newest_green_unreleased
    if sha is None:
        return ReleaseStaleness(False)
    since = commit_times.get(sha)
    behind = len(scan.green_unreleased)
    too_old = since is not None and now - since > timedelta(hours=max_age_hours)
    if not too_old and behind <= max_green_behind:
        return ReleaseStaleness(False)
    when = f" since {since.isoformat()}" if since is not None else ""
    return ReleaseStaleness(
        True,
        f"no release published for green main {sha[:7]}{when} ({behind} green commit(s) behind)",
    )
