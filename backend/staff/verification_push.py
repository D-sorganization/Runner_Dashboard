"""PR-branch push evidence for staff run verification (#4492).

pr-remediator runs often push their fix straight to the target PR's own head branch instead of
their ``staff/...`` branch, so the staff branch is never ahead of main and the run used to be
verified ``no_change``. This module decides whether the target PR's head was committed by the run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from staff.verification_types import PrProbe, PullRequest

if TYPE_CHECKING:
    from staff.store import RunRecord

# Committers that are GitHub itself (web "Update branch", merge queue), never the run's own push.
GITHUB_COMMITTERS = ("web-flow", "github")
# Clock skew allowed between the run's host and the committing host when crediting a PR-branch push.
PUSH_CLOCK_SKEW = timedelta(minutes=2)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def pushed_to_target_branch(rec: RunRecord, target: PullRequest, probe: PrProbe, now: datetime) -> bool:
    """True when the target PR's head was committed during this run, i.e. the run pushed to the PR's own branch.

    #4492: pr-remediator runs often push fixes straight to the existing PR head branch instead of their
    ``staff/...`` branch, so the staff branch is never ahead of main. The head commit's committer date must
    fall inside ``[started_at - skew, ended_at + skew]`` and the committer must not be GitHub itself (an
    "Update branch" merge). Needs the probe's optional ``commit_info``; without it (or without a start time
    or head SHA) the answer is False. Raises GitHubLookupError when the commit lookup fails.
    """
    commit_info = getattr(probe, "commit_info", None)
    start = _parse_ts(getattr(rec, "started_at", None))
    if not callable(commit_info) or not target.head_sha or start is None:
        return False
    end = _parse_ts(rec.ended_at) or now
    info = commit_info(rec.repo, target.head_sha)
    committed = _parse_ts(info.committed_at)
    if committed is None or info.committer.strip().lower() in GITHUB_COMMITTERS:
        return False
    return start - PUSH_CLOCK_SKEW <= committed <= end + PUSH_CLOCK_SKEW
