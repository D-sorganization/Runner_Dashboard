"""Post-run verification of staff runs (WP-1.1, #1516).

A run is ``succeeded`` when its CLI exits 0 with a ``STAFF_RESULT:`` line; nothing
checks the promised PR. This module checks it: the PR for the run's branch must be
open or merged with head CI green. The verdict is recorded beside the status and is
kept apart from the classifier, so a GitHub outage can never make a run look failed.

Verdicts: ``verified`` | ``unverified`` (CI pending, or GitHub unreachable) |
``failed`` (commits were pushed but no PR, PR closed unmerged, head CI red, or CI
still pending after :data:`PENDING_LIMIT`) | ``no_change`` (the run claimed success but
pushed no commits to its staff branch on the remote) | ``not_applicable`` (the run
claimed nothing, its role does not open PRs, or it reported an explicit skip / "no work" result
with nothing pushed). Red head CI within ``STAFF_VERIFY_CI_GRACE_SECONDS`` (default 1800) of the
run ending is ``unverified``, not ``failed``: CI starts seconds after the push, so a verdict taken at
exit races it. A ``failed`` CI verdict is rechecked while the run is younger than
:data:`RECHECK_WINDOW`, and a later green flips it to ``verified``. ``STAFF_VERIFY_MODE``: ``report``
(default) records only; ``enforce`` also fails a ``succeeded`` run whose verdict is
``failed``; ``off`` skips the check.
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

from staff.completion import ARTIFACT_PR_MERGED, ARTIFACT_PR_OPEN
from staff.roles import AD_HOC_ROLE, load_roles
from staff.workspace import ORG

if TYPE_CHECKING:
    from staff.store import RunRecord, RunStore

log = logging.getLogger("dashboard.staff.verification")

MODE_ENV = "STAFF_VERIFY_MODE"
MODES = ("off", "report", "enforce")
UNVERIFIED_OUTPUT = "unverified_output"
PENDING_LIMIT = timedelta(hours=6)
RECHECK_WINDOW = timedelta(hours=24)
CI_GRACE_ENV = "STAFF_VERIFY_CI_GRACE_SECONDS"
DEFAULT_CI_GRACE_SECONDS = 1800
# A STAFF_RESULT summary that starts with one of these (case-insensitive) is a deliberate
# skip / no-op, not a failed deliverable. Matched at the start only, to stay conservative.
SKIP_MARKERS = ("staff_skip", "skip", "no work", "nothing to do", "no-op", "noop", "no action", "no change")
RESULT_PREFIX = "STAFF_RESULT:"
CI_RED_DETAIL = "head CI is failing"
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
    artifact: str = ""  # typed completion evidence (#1808): pr_open | pr_merged | ""
    ci_red: bool = False  # failed because head CI is red (the run's work may still be good)


class PrProbe(Protocol):
    def find(self, repo: str, branch: str) -> PullRequest | None:
        """The newest PR whose head is ``branch`` in ``repo``, or None. Raises GitHubLookupError."""

    def get(self, repo: str, number: int) -> PullRequest:
        """PR ``number`` in ``repo`` with head CI state. Raises GitHubLookupError."""

    def remote_ahead_by(self, repo: str, branch: str, base: str = "main") -> int:
        """Commits ``branch`` is ahead of ``base`` on the remote (0 if absent). Raises GitHubLookupError."""


def verify_mode() -> str:
    """``STAFF_VERIFY_MODE``, defaulting to ``report`` when unset or unknown."""
    raw = os.environ.get(MODE_ENV, "").strip().lower()
    if raw and raw not in MODES:
        log.warning("unknown %s=%r; using report", MODE_ENV, raw)
    return raw if raw in MODES else "report"


def ci_grace() -> timedelta:
    """``STAFF_VERIFY_CI_GRACE_SECONDS`` as a timedelta (default 1800s; invalid or negative uses the default)."""
    raw = os.environ.get(CI_GRACE_ENV, "").strip()
    try:
        seconds = float(raw) if raw else float(DEFAULT_CI_GRACE_SECONDS)
    except ValueError:
        seconds = float(DEFAULT_CI_GRACE_SECONDS)
    return timedelta(seconds=seconds if seconds >= 0 else DEFAULT_CI_GRACE_SECONDS)


def is_skip_result(result_text: str) -> bool:
    """True when the run's final ``STAFF_RESULT:`` summary is an explicit skip / no-work report."""
    _, sep, rest = (result_text or "").rpartition(RESULT_PREFIX)
    if not sep:
        return False
    lines = rest.strip().splitlines()
    summary = lines[0].strip().lower().lstrip("[(*_ ") if lines else ""
    return bool(summary) and summary.startswith(SKIP_MARKERS)


def ci_state(check_runs: Iterable[Mapping[str, Any]]) -> str:
    """``red`` if any head check failed, else ``pending`` until all counted checks completed."""
    counted = [r for r in check_runs if r.get("conclusion") not in _IGNORED_CONCLUSIONS]
    if any(r.get("conclusion") in _RED_CONCLUSIONS for r in counted):
        return "red"
    if not counted or any(r.get("status") != "completed" for r in counted):
        return "pending"
    return "green"


def decide(
    *,
    claimed: str,
    opens_pr: bool,
    branch: str,
    pr: PullRequest | None,
    remote_ahead_by: int = 1,
    skipped: bool = False,
    ci_grace_active: bool = False,
) -> Verdict:
    """The verdict for one run. Pure: every input is a plain value.

    ``skipped``: the run reported an explicit skip. With no PR and nothing pushed that is neutral.
    ``ci_grace_active``: the run ended within the CI grace window, so red CI is still ``unverified``.
    """
    if claimed != "succeeded":
        return Verdict("not_applicable", f"run ended {claimed}; nothing claimed to verify")
    if not opens_pr:
        return Verdict("not_applicable", "role does not open pull requests")
    if pr is None:
        if skipped and (not branch or remote_ahead_by == 0):
            return Verdict("not_applicable", "run reported it skipped (no work requested); nothing pushed")
        if branch and remote_ahead_by == 0:
            return Verdict("no_change", f"no commits pushed to remote branch {branch}")
        reason = f"no pull request found for branch {branch}" if branch else "run recorded no branch"
        return Verdict("failed", reason)
    if pr.state == "closed":
        return Verdict("failed", f"PR #{pr.number} was closed without merging", pr.number)
    artifact = ARTIFACT_PR_MERGED if pr.state == "merged" else ARTIFACT_PR_OPEN
    if pr.ci == "red":
        if ci_grace_active:
            detail = f"PR #{pr.number} head CI is failing; rechecking within the grace window"
            return Verdict("unverified", detail, pr.number, artifact)
        return Verdict("failed", f"PR #{pr.number} {CI_RED_DETAIL}", pr.number, artifact, ci_red=True)
    if pr.ci == "pending":
        return Verdict("unverified", f"PR #{pr.number} head CI is still pending", pr.number, artifact)
    return Verdict("verified", f"PR #{pr.number} is {pr.state} with head CI green", pr.number, artifact)


def _age(rec: RunRecord, now: datetime) -> timedelta:
    if not rec.ended_at:
        return timedelta(0)
    return now - datetime.fromisoformat(rec.ended_at.replace("Z", "+00:00"))


def _claimed_status(rec: RunRecord) -> str:
    """The status the run claimed: a run failed by verification alone still claimed ``succeeded``."""
    if rec.status == "failed" and rec.failure_class == UNVERIFIED_OUTPUT and rec.verification == "failed":
        return "succeeded"
    return rec.status


def _target_pr_number(rec: RunRecord) -> int | None:
    """PR number for a ``target_kind=pr`` run from ``pr_number`` or ``target_ref``."""
    if rec.pr_number is not None:
        return int(rec.pr_number)
    if rec.target_kind != "pr":
        return None
    ref = rec.target_ref.strip()
    if ref.isdigit():
        return int(ref)
    upper = ref.upper()
    if upper.startswith("PR #"):
        digits = ref[4:].strip()
        return int(digits) if digits.isdigit() else None
    return None


def evaluate(
    rec: RunRecord,
    *,
    opens_pr: bool,
    probe: PrProbe,
    now: datetime,
    result_text: str = "",
    grace: timedelta | None = None,
) -> Verdict:
    """Look up the run's PR (only when one is expected) and decide.

    Post: a GitHub error yields ``unverified``; CI still pending after PENDING_LIMIT yields ``failed``;
    an ad-hoc run with no repository is ``not_applicable`` and GitHub is not asked. Red CI within
    the grace window of ``ended_at`` is ``unverified``; a skip ``result_text`` with nothing pushed is
    ``not_applicable``.
    """
    if rec.role == AD_HOC_ROLE and not rec.repo:
        return Verdict("not_applicable", "ad-hoc run had no repository, so no pull request was expected")
    pr: PullRequest | None = None
    remote_ahead_by = 1
    claimed = _claimed_status(rec)
    if claimed == "succeeded" and opens_pr and rec.branch:
        try:
            pr = probe.find(rec.repo, rec.branch)
        except GitHubLookupError as exc:
            return Verdict("unverified", f"GitHub lookup failed: {exc}")
        if pr is None:
            try:
                remote_ahead_by = probe.remote_ahead_by(rec.repo, rec.branch)
            except GitHubLookupError as exc:
                return Verdict("unverified", f"GitHub lookup failed: {exc}")
        get_pr = getattr(probe, "get", None)
        target_pr = _target_pr_number(rec) if rec.target_kind == "pr" else None
        if pr is None and target_pr is not None and callable(get_pr):
            try:
                target = get_pr(rec.repo, target_pr)
            except GitHubLookupError as exc:
                return Verdict("unverified", f"GitHub lookup failed: {exc}")
            if remote_ahead_by == 0 and target.state == "merged":
                return Verdict(
                    "not_applicable",
                    f"PR #{target_pr} already merged; no commits pushed",
                    target_pr,
                    ARTIFACT_PR_MERGED,
                )
            if remote_ahead_by > 0:
                pr = target
    verdict = decide(
        claimed=claimed,
        opens_pr=opens_pr,
        branch=rec.branch,
        pr=pr,
        remote_ahead_by=remote_ahead_by,
        skipped=is_skip_result(result_text),
        ci_grace_active=_age(rec, now) <= (ci_grace() if grace is None else grace),
    )
    if verdict.verification == "unverified" and _age(rec, now) > PENDING_LIMIT:
        hours = int(PENDING_LIMIT.total_seconds() // 3600)
        return Verdict("failed", f"{verdict.detail} {hours}h after the run ended", verdict.pr_number)
    return verdict


def apply_deliverable_failure(
    store: RunStore,
    rec: RunRecord,
    verdict: Verdict | None,
    *,
    opens_pr: bool | Callable[[], bool],
) -> RunRecord | None:
    """Apply deliverable-failure fields when a succeeded run produced no PR/commits."""
    if verdict is None or verdict.verification not in ("no_change", "failed"):
        return rec
    try:
        requires_pr = opens_pr() if callable(opens_pr) else opens_pr
    except Exception:  # noqa: BLE001 - a role resolver must not crash run completion
        log.warning("could not resolve deliverable policy for run %s", rec.id, exc_info=True)
        return rec
    updates = deliverable_failure_updates(rec, verdict, opens_pr=requires_pr)
    if not updates:
        return rec
    store.update_run(rec.id, **updates)
    return store.get_run(rec.id)


def deliverable_failure_updates(
    rec: RunRecord,
    verdict: Verdict | None,
    *,
    opens_pr: bool,
) -> dict[str, Any]:
    """Fail succeeded runs that targeted an issue/PR but delivered no commits or PR (#2171)."""
    if rec.status != "succeeded" or not opens_pr or rec.target_kind not in ("issue", "pr"):
        return {}
    if verdict is None or verdict.verification not in ("no_change", "failed"):
        return {}
    if verdict.ci_red:
        remediation = (
            "The run opened a pull request but its head CI is failing past the grace window; "
            "fix the failing checks (a later green recheck restores this run)."
        )
    else:
        remediation = (
            "Run targeted an issue or pull request but produced no commits or open PR; "
            "inspect the transcript before re-dispatching."
        )
    return {
        "status": "failed",
        "failure_class": UNVERIFIED_OUTPUT,
        "retryable": False,
        "error": verdict.detail,
        "remediation": remediation,
    }


def updates_for(rec: RunRecord, verdict: Verdict, mode: str) -> dict[str, Any]:
    """The run fields to write. Only ``enforce`` changes the status of a succeeded run."""
    updates: dict[str, Any] = {
        "verification": verdict.verification,
        "verification_detail": verdict.detail,
        "pr_number": verdict.pr_number,
        "artifact": verdict.artifact,
    }
    if mode == "enforce" and verdict.verification == "failed" and rec.status == "succeeded":
        updates.update(status="failed", failure_class=UNVERIFIED_OUTPUT, error=verdict.detail)
    elif verdict.verification == "verified" and rec.status == "failed" and rec.failure_class == UNVERIFIED_OUTPUT:
        # The failure was verification alone (CI raced the check); CI is green now, so restore the run.
        updates.update(status="succeeded", failure_class="", error="", remediation="", retryable=False)
    return updates


def _record_role_open_pr_diagnostic(store: RunStore, rec: RunRecord, resolver_path: str) -> None:
    """Telemetry for unexpected ``opens_pr=False`` verdict paths (diagnosis class E)."""
    payload: dict[str, Any] = {
        "run_id": rec.id,
        "role": rec.role,
        "resolver_path": resolver_path,
        "role_found": False,
        "role_permissions_open_pr": None,
    }
    try:
        spec = load_roles().get(rec.role)
        payload["role_found"] = spec is not None
        if spec is not None:
            payload["role_permissions_open_pr"] = bool((spec.permissions or {}).get("open_pr"))
            payload["role_surface"] = spec.surface
            payload["role_providers"] = list(spec.providers)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not affect verification
        payload["role_lookup_error"] = str(exc)[:200]
    store.append_event(rec.id, "verify_diag", json.dumps(payload, sort_keys=True))
    log.warning("verification role-open-pr diagnostic: %s", payload)


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
        resolver_path = "callable" if callable(opens_pr) else "direct-value"
        try:
            is_opens_pr = opens_pr() if callable(opens_pr) else bool(opens_pr)
            verdict = evaluate(
                rec,
                opens_pr=is_opens_pr,
                probe=probe or GhCliPrProbe(),
                now=now or datetime.now(UTC),
                result_text=store.last_result_line(run_id),
            )
        except Exception as exc:  # noqa: BLE001 - verification must never break a run
            log.exception("verification of run %s failed", run_id)
            verdict = Verdict("unverified", f"verification error: {exc}")
        if verdict.verification == "not_applicable" and verdict.detail == "role does not open pull requests":
            _record_role_open_pr_diagnostic(store, rec, resolver_path)
        store.update_run(run_id, **updates_for(rec, verdict, mode))
        store.append_event(run_id, "verify", f"{verdict.verification}: {verdict.detail}")
        if verdict.verification == "verified":
            try:
                from staff.review import auto_review_if_eligible

                auto_review_if_eligible(rec, verdict, store=store)
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
    """Verify this node's finished runs that are unchecked, ``unverified``, or failed on CI.

    Bounded: runs that ended within RECHECK_WINDOW, at most RECHECK_BATCH per pass. Older rows are
    never touched, so the first scheduler tick cannot mass-fail historic ``unverified`` rows.
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

    def _full(self, repo: str) -> str:
        return repo if "/" in repo else f"{self.org}/{repo}"

    def _with_ci(self, full: str, pr: Mapping[str, Any]) -> PullRequest:
        state = "merged" if pr.get("merged_at") else str(pr.get("state") or "open")
        checks = self._api(f"/repos/{full}/commits/{pr['head']['sha']}/check-runs?per_page=100")
        return PullRequest(number=int(pr["number"]), state=state, ci=ci_state(checks.get("check_runs") or []))

    def find(self, repo: str, branch: str) -> PullRequest | None:
        full = self._full(repo)
        owner = full.split("/", 1)[0]
        head = quote(f"{owner}:{branch}", safe=":")
        pulls = self._api(f"/repos/{full}/pulls?head={head}&state=all&per_page=1")
        return self._with_ci(full, pulls[0]) if pulls else None

    def remote_ahead_by(self, repo: str, branch: str, base: str = "main") -> int:
        """How many commits ``branch`` is ahead of ``base`` on the remote (0 if the branch is missing)."""
        full = self._full(repo)
        path = f"/repos/{full}/compare/{quote(base, safe='')}...{quote(branch, safe='')}"
        try:
            body = self._api(path)
        except GitHubLookupError as exc:
            msg = str(exc).lower()
            if "404" in msg or "not found" in msg:
                return 0
            raise
        return int(body.get("ahead_by") or 0)

    def get(self, repo: str, number: int) -> PullRequest:
        """PR ``number`` in ``repo`` with its head CI state (the Code Request acceptance check, #1605)."""
        full = self._full(repo)
        return self._with_ci(full, self._api(f"/repos/{full}/pulls/{int(number)}"))
