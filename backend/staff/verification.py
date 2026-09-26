"""Post-run verification of staff runs (WP-1.1, #1516).

A run is ``succeeded`` when its CLI exits 0 with a ``STAFF_RESULT:`` line; nothing
checks the promised PR. This module checks it: the PR for the run's branch must be
open or merged with head CI green. The verdict is recorded beside the status and is
kept apart from the classifier, so a GitHub outage can never make a run look failed.

Verdicts: ``verified`` | ``unverified`` (CI pending, or GitHub unreachable) |
``failed`` (no PR, PR closed unmerged, head CI red, or CI still pending after
:data:`PENDING_LIMIT`) | ``not_applicable`` (the run claimed nothing, or its role does
not open PRs). ``STAFF_VERIFY_MODE``: ``report`` (default) records only; ``enforce``
also fails a ``succeeded`` run whose verdict is ``failed``; ``off`` skips the check.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote

from staff.workspace import ORG

if TYPE_CHECKING:
    from staff.store import RunRecord, RunStore

log = logging.getLogger("dashboard.staff.verification")

MODE_ENV = "STAFF_VERIFY_MODE"
MODES = ("off", "report", "enforce")
UNVERIFIED_OUTPUT = "unverified_output"
PENDING_LIMIT = timedelta(hours=6)
RECHECK_WINDOW = timedelta(hours=24)
RECHECK_BATCH = 20
GH_TIMEOUT_SECONDS = 30

# Check-run conclusions that fail the head. ``cancelled``/``stale`` runs were superseded
# and neither pass nor fail it; ``success``/``neutral``/``skipped`` pass.
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


class PrProbe(Protocol):
    def find(self, repo: str, branch: str) -> PullRequest | None:
        """The newest PR whose head is ``branch`` in ``repo``, or None. Raises GitHubLookupError."""


def verify_mode() -> str:
    """``STAFF_VERIFY_MODE``, defaulting to ``report`` when unset or unknown."""
    raw = os.environ.get(MODE_ENV, "").strip().lower()
    if raw and raw not in MODES:
        log.warning("unknown %s=%r; using report", MODE_ENV, raw)
    return raw if raw in MODES else "report"


def ci_state(check_runs: Iterable[Mapping[str, Any]]) -> str:
    """``red`` if any head check failed, else ``pending`` until all counted checks completed."""
    counted = [r for r in check_runs if r.get("conclusion") not in _IGNORED_CONCLUSIONS]
    if any(r.get("conclusion") in _RED_CONCLUSIONS for r in counted):
        return "red"
    if not counted or any(r.get("status") != "completed" for r in counted):
        return "pending"
    return "green"


def decide(*, claimed: str, opens_pr: bool, branch: str, pr: PullRequest | None) -> Verdict:
    """The verdict for one run. Pure: every input is a plain value."""
    if claimed != "succeeded":
        return Verdict("not_applicable", f"run ended {claimed}; nothing claimed to verify")
    if not opens_pr:
        return Verdict("not_applicable", "role does not open pull requests")
    if pr is None:
        reason = f"no pull request found for branch {branch}" if branch else "run recorded no branch"
        return Verdict("failed", reason)
    if pr.state == "closed":
        return Verdict("failed", f"PR #{pr.number} was closed without merging", pr.number)
    if pr.ci == "red":
        return Verdict("failed", f"PR #{pr.number} head CI is failing", pr.number)
    if pr.ci == "pending":
        return Verdict("unverified", f"PR #{pr.number} head CI is still pending", pr.number)
    return Verdict("verified", f"PR #{pr.number} is {pr.state} with head CI green", pr.number)


def _age(rec: RunRecord, now: datetime) -> timedelta:
    if not rec.ended_at:
        return timedelta(0)
    return now - datetime.fromisoformat(rec.ended_at.replace("Z", "+00:00"))


def evaluate(rec: RunRecord, *, opens_pr: bool, probe: PrProbe, now: datetime) -> Verdict:
    """Look up the run's PR (only when one is expected) and decide.

    Post: a GitHub error yields ``unverified``; CI still pending after PENDING_LIMIT yields ``failed``.
    """
    pr: PullRequest | None = None
    if rec.status == "succeeded" and opens_pr and rec.branch:
        try:
            pr = probe.find(rec.repo, rec.branch)
        except GitHubLookupError as exc:
            return Verdict("unverified", f"GitHub lookup failed: {exc}")
    verdict = decide(claimed=rec.status, opens_pr=opens_pr, branch=rec.branch, pr=pr)
    if verdict.verification == "unverified" and _age(rec, now) > PENDING_LIMIT:
        hours = int(PENDING_LIMIT.total_seconds() // 3600)
        return Verdict("failed", f"{verdict.detail} {hours}h after the run ended", verdict.pr_number)
    return verdict


def updates_for(rec: RunRecord, verdict: Verdict, mode: str) -> dict[str, Any]:
    """The run fields to write. Only ``enforce`` changes the status of a succeeded run."""
    updates: dict[str, Any] = {
        "verification": verdict.verification,
        "verification_detail": verdict.detail,
        "pr_number": verdict.pr_number,
    }
    if mode == "enforce" and verdict.verification == "failed" and rec.status == "succeeded":
        updates.update(status="failed", failure_class=UNVERIFIED_OUTPUT, error=verdict.detail)
    return updates


def verify_and_record(
    store: RunStore,
    run_id: str,
    *,
    opens_pr: bool | Callable[[], bool],
    probe: PrProbe | None = None,
    mode: str | None = None,
    now: datetime | None = None,
) -> Verdict | None:
    """Verify one finished run and persist the verdict with a ``verify`` event.

    Returns None in ``off`` mode, for an unknown run, or on unexpected error.
    Never raises: an unexpected error in evaluation, resolution, or store persistence
    is caught and logged, so the runner's finish path is never affected.

    Postcondition: this function never raises an exception.
    """
    try:
        mode = mode or verify_mode()
        rec = store.get_run(run_id)
        if mode == "off" or rec is None:
            return None
        try:
            is_opens_pr = opens_pr() if callable(opens_pr) else bool(opens_pr)
            verdict = evaluate(rec, opens_pr=is_opens_pr, probe=probe or GhCliPrProbe(), now=now or datetime.now(UTC))
        except Exception as exc:  # noqa: BLE001 - verification must never break a run
            log.exception("verification of run %s failed", run_id)
            verdict = Verdict("unverified", f"verification error: {exc}")
        store.update_run(run_id, **updates_for(rec, verdict, mode))
        store.append_event(run_id, "verify", f"{verdict.verification}: {verdict.detail}")
        if verdict.verification == "verified":
            try:
                from staff.review import auto_review_if_eligible

                auto_review_if_eligible(rec, verdict, store=store, gh_probe=GhCliPrProbe())
            except Exception:  # noqa: BLE001
                log.warning("auto_review_if_eligible failed for run %s", run_id, exc_info=True)
        return verdict
    except Exception:  # noqa: BLE001 - verification must never break a run
        log.warning("verify_and_record failed for run %s", run_id, exc_info=True)
        return None


def recheck_runs(
    store: RunStore,
    *,
    machine: str,
    opens_pr: Callable[[str], bool],
    probe: PrProbe | None = None,
    mode: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Verify this node's finished runs that are unchecked or ``unverified``.

    Bounded: runs that ended within RECHECK_WINDOW, at most RECHECK_BATCH per pass.
    Returns the ids checked.
    """
    mode = mode or verify_mode()
    if mode == "off":
        return []
    now = now or datetime.now(UTC)
    since = (now - RECHECK_WINDOW).isoformat().replace("+00:00", "Z")
    probe = probe or GhCliPrProbe()
    runs = store.runs_awaiting_verification(machine=machine, since=since, limit=RECHECK_BATCH)
    for rec in runs:
        verify_and_record(store, rec.id, opens_pr=partial(opens_pr, rec.role), probe=probe, mode=mode, now=now)
    return [rec.id for rec in runs]


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class GhCliPrProbe:
    """Finds a run's PR with two scoped ``gh api`` REST calls (the PR, then its head's check runs).

    Uses the ``gh`` CLI so it works from the runner's plain worker threads, where the
    event-loop-bound ``gh_client`` is unreachable (same approach as ``consolidation``).
    """

    def __init__(self, org: str = ORG, run: Runner = subprocess.run) -> None:
        self.org, self._run = org, run

    def _api(self, path: str) -> Any:
        try:
            proc = self._run(
                ["gh", "api", path], capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitHubLookupError(f"gh api {path}: {exc}") from exc
        if proc.returncode != 0:
            raise GitHubLookupError((proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[:300])
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:
            raise GitHubLookupError(f"gh api {path}: invalid JSON") from exc

    def find(self, repo: str, branch: str) -> PullRequest | None:
        full = repo if "/" in repo else f"{self.org}/{repo}"
        owner = full.split("/", 1)[0]
        head = quote(f"{owner}:{branch}", safe=":")
        pulls = self._api(f"/repos/{full}/pulls?head={head}&state=all&per_page=1")
        if not pulls:
            return None
        pr = pulls[0]
        state = "merged" if pr.get("merged_at") else str(pr.get("state") or "open")
        checks = self._api(f"/repos/{full}/commits/{pr['head']['sha']}/check-runs?per_page=100")
        return PullRequest(number=int(pr["number"]), state=state, ci=ci_state(checks.get("check_runs") or []))

    def get_commit_messages(self, repo: str, pr_number: int) -> list[str]:
        """Commit messages of one PR (one scoped REST call), for the reviewer's author lookup (#1579)."""
        full = repo if "/" in repo else f"{self.org}/{repo}"
        commits = self._api(f"/repos/{full}/pulls/{int(pr_number)}/commits?per_page=100")
        return [str((c.get("commit") or {}).get("message") or "") for c in commits or []]
