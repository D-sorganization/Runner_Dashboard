"""Concrete hub capabilities for review-arm: GitHub reads, dispatch, arm, relay, alerts.

Runs on the review-arm worker thread. Every async call (``gh_client``,
``dispatch_staff_run``) is submitted to the dashboard's own event loop, so the
shared GitHub client, its App auth and its rate-limit governor stay
authoritative. Writes happen only through :class:`RDHubPort` methods that the
service and processor never call in dry-run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Iterable, Mapping
from dataclasses import replace
from typing import Any, TypeVar

from staff.review_arm.armer import ArmOutcome, GitHubArmer
from staff.review_arm.github_source import LIST_QUERY, GitHubSource
from staff.review_arm.intake import Backoff, Intake
from staff.review_arm.ledger import Ledger
from staff.review_arm.policy import Candidate, Decision, LiveHead
from staff.review_arm.settings import Settings
from staff.review_arm.verdicts import RunOutcome, extract_outcome

log = logging.getLogger(__name__)

ORG = "D-sorganization"
REVIEWER_ROLE = "code-reviewer"
REQUESTER = "staff:review-arm"
CALL_TIMEOUT = 180.0
COMMENT_LIMIT = 60_000
T = TypeVar("T")
Notify = Callable[[str, str, str, str], None]  # title, text, recommended_action, dedupe_key

CHECKS_QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      headRefOid
      commits(last: 1) { nodes { commit { oid statusCheckRollup { contexts(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          __typename
          ... on CheckRun { name status conclusion isRequired(pullRequestNumber: $number) }
          ... on StatusContext { context state isRequired(pullRequestNumber: $number) }
        }
      } } } } }
    }
  }
}"""
_GREEN_RUN = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})


def required_checks_green(data: Mapping[str, Any]) -> tuple[bool, str]:
    """(green, commit oid): every required check passed and at least one exists.

    Mirrors ``gh pr checks --required`` in intake.py: no required check, a
    pending one, a failed one or a truncated list is not green.
    """
    pr = (data.get("repository") or {}).get("pullRequest") or {}
    nodes = ((pr.get("commits") or {}).get("nodes")) or []
    commit = (nodes[-1] or {}).get("commit") if nodes else None
    if not commit:
        return False, ""
    contexts = ((commit.get("statusCheckRollup") or {}).get("contexts")) or {}
    if (contexts.get("pageInfo") or {}).get("hasNextPage", True):
        return False, str(commit.get("oid") or "")
    required = [n for n in contexts.get("nodes") or [] if n.get("isRequired")]
    green = bool(required) and all(
        (n.get("status") == "COMPLETED" and n.get("conclusion") in _GREEN_RUN)
        if n.get("__typename") == "CheckRun"
        else n.get("state") == "SUCCESS"
        for n in required
    )
    return green, str(commit.get("oid") or "")


def review_prompt(c: Candidate, round_no: int) -> str:
    """intake.py focus text plus the policy-2 output contract."""
    focus = (
        f"Auto-intake: exact-head review of {c.repo}#{c.pr} (green, mergeable, unreviewed at this head)."
        if round_no == 1
        else f"Auto-intake round {round_no}: new head {c.head_sha[:8]} since last review. Delta review; check "
        "earlier BLOCKING findings are fixed and no new BLOCKING defect."
    )
    return (
        f"Review PR #{c.pr} in {c.repo} at head {c.head_sha}. {focus} "
        "Tag every finding [BLOCKING] or [NONBLOCKING] and cite file:line; name the tests you ran or read. "
        "Read only: do not comment on, approve or merge the PR (the hub relays your final message). "
        f"End with 'STAFF_RESULT: review approve|changes|escalate #{c.pr} @{c.head_sha[:7]}'."
    )


class RDHubPort:
    def __init__(
        self,
        *,
        settings: Settings,
        ledger: Ledger,
        loop: asyncio.AbstractEventLoop,
        store: Any,
        notify: Notify,
    ) -> None:
        self._settings = settings
        self.ledger = ledger
        self._loop = loop
        self._store = store
        self._notify = notify
        self.armer = GitHubArmer(graphql=self.graphql, rest_get=self.rest_get)

    # ── loop bridge / GitHub ──
    def _await(self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(CALL_TIMEOUT)

    def graphql(self, query: str, variables: dict[str, Any]) -> Mapping[str, Any]:
        import gh_client  # noqa: PLC0415

        mutation = query.lstrip().startswith("mutation")
        try:
            payload = self._await(
                gh_client.post("graphql", json={"query": query, "variables": variables}, idempotent=not mutation)
            )
        except gh_client.GhRateLimited as exc:
            raise Backoff(f"GitHub rate limited; retry after {exc.retry_after_seconds}s") from exc
        if not isinstance(payload, Mapping) or payload.get("errors") or not isinstance(payload.get("data"), Mapping):
            raise OSError("GitHub GraphQL response unavailable")
        return payload["data"]

    def rest_get(self, path: str) -> Any:
        import gh_client  # noqa: PLC0415

        try:
            return self._await(gh_client.get(path))
        except gh_client.GhRateLimited as exc:
            raise Backoff(f"GitHub rate limited; retry after {exc.retry_after_seconds}s") from exc

    def holds(self) -> frozenset[tuple[str, int]]:
        return self._settings.held_prs | self.ledger.holds()

    # ── HubPort (intake side) ──
    def candidates(self) -> Iterable[Candidate]:
        source = GitHubSource(
            context=self._context,
            confirm=self._confirm,
            query=lambda owner, repo: self.graphql(LIST_QUERY, {"owner": owner, "name": repo}),
        )
        return Intake(source, repos=self._settings.repos, held_prs=self.holds()).candidates()

    def _context(self, c: Candidate) -> Candidate:
        from staff import review  # noqa: PLC0415

        blocked, approved = self.ledger.state(c.repo, c.pr, c.head_sha)
        attempts = self.ledger.attempts_at(c.repo, c.pr, c.head_sha)
        runs = self._store.list_runs_for_target(role=REVIEWER_ROLE, repo=c.repo, target_ref=f"PR #{c.pr}")
        other_review = review._already_reviewed(self._store, c.repo, c.pr, head_sha=c.head_sha)
        return replace(
            c,
            holds_available=True,
            held=(c.repo, c.pr) in self.holds(),
            blocked=blocked,
            approved=approved,
            queued=attempts["pending"] > 0 or other_review,
            reviewed=attempts["done"] > 0 or attempts["failed"] >= 2,
            prior_runs=max(self.ledger.rounds(c.repo, c.pr), len(runs)),
        )

    def _confirm(self, c: Candidate) -> Candidate:
        from staff import review  # noqa: PLC0415

        green, oid = required_checks_green(self.graphql(CHECKS_QUERY, {"owner": ORG, "name": c.repo, "number": c.pr}))
        author = review.detect_author_provider(c.repo, c.pr, store=self._store, gh_probe=review.GhCliCommitProbe())
        return replace(c, checks_green=green, checks_head=oid, author_provider=author or "")

    def refresh(self, candidate: Candidate) -> Candidate:
        live = self.armer.live(candidate.repo, candidate.pr)
        fresh = replace(
            candidate,
            head_sha=live.head_sha,
            state=live.state,
            draft=live.draft,
            labels=live.labels,
            title=live.title or candidate.title,
        )
        return self._confirm(self._context(fresh))

    def dispatch(self, candidate: Candidate, decision: Decision) -> None:
        from identity import Principal  # noqa: PLC0415
        from staff.dispatch_service import DispatchCommand, dispatch_staff_run  # noqa: PLC0415

        round_no = candidate.prior_runs + 1
        cmd = DispatchCommand(
            role=REVIEWER_ROLE,
            requested_by=REQUESTER,
            provider=decision.provider,
            repo=candidate.repo,
            pr=candidate.pr,
            prompt=review_prompt(candidate, round_no),
            requested_skill="fleet-review",
            dispatch_origin="review-arm",
            strict_provider=True,
            authority_code_read_only=True,
        )
        caller = Principal(id=REQUESTER, type="bot", name="RD review-arm", scopes=["staff.dispatch"])
        result = self._await(dispatch_staff_run(cmd, caller))
        run_id = str(((result or {}).get("run") or {}).get("id") or "")
        if not run_id:
            raise RuntimeError(f"dispatch returned no run for {candidate.repo}#{candidate.pr}")
        self.ledger.record_dispatch(
            candidate.repo,
            candidate.pr,
            candidate.head_sha,
            run_id=run_id,
            round_no=round_no,
            provider=decision.provider,
        )

    def arm(self, candidate: Candidate) -> None:
        out = self.armer.arm(candidate.repo, candidate.pr, candidate.head_sha, holds=self.holds())
        self.ledger.record_arm(candidate.repo, candidate.pr, candidate.head_sha, armed=out.armed, detail=out.detail)
        if not out.armed:
            raise RuntimeError(f"not armed: {out.detail}")

    def escalate(self, candidate: Candidate, reason: str) -> None:
        """Intake escalation (round cap, unknown author): deduplicated per head and reason."""
        c = candidate
        if self.ledger.record_escalation(c.repo, c.pr, c.head_sha, reason):
            self.send_escalation(c.repo, c.pr, c.head_sha, reason, f"{c.prior_runs} prior review rounds")

    def send_escalation(self, repo: str, pr: int, head: str, reason: str, detail: str) -> None:
        """Notify the owner; the caller has already recorded (deduplicated) it."""
        self._notify(
            f"review-arm needs owner: {repo}#{pr}",
            f"{repo}#{pr} @{head[:8]}: {reason} ({detail}); review-arm will not re-dispatch it.",
            "Dieter: decide (fix, override the head, or hold); review-arm stops looping on this PR.",
            f"review-arm:{repo}#{pr}@{head[:12]}:{reason}",
        )

    # ── ProcessorPort (verdict side) ──
    def run_outcome(self, run_id: str) -> RunOutcome:
        rec = self._store.get_run(run_id)
        if rec is None:
            return RunOutcome("no_verdict")
        events: list[dict[str, Any]] = []
        after = 0
        while True:
            page = self._store.events_after(run_id, after, 500)
            events += page
            if len(page) < 500:
                break
            after = int(page[-1]["seq"])
        return extract_outcome(str(rec.status or ""), str(rec.outcome or ""), events)

    def live(self, repo: str, pr: int) -> LiveHead:
        return self.armer.live(repo, pr)

    def arm_head(self, repo: str, pr: int, head: str, *, holds: frozenset[tuple[str, int]]) -> ArmOutcome:
        return self.armer.arm(repo, pr, head, holds=holds)

    def post_findings(self, repo: str, pr: int, head: str, run_id: str, text: str) -> None:
        import gh_client  # noqa: PLC0415

        body = f"Exact-head review relay ({run_id}) at `{head[:8]}`; the reviewer's final output verbatim:\n\n{text}"
        self._await(gh_client.post(f"/repos/{ORG}/{repo}/issues/{pr}/comments", json={"body": body[:COMMENT_LIMIT]}))


class ProcessorAdapter:
    """Adapts :class:`RDHubPort` to the processor's port names."""

    def __init__(self, port: RDHubPort) -> None:
        self._port = port

    def run_outcome(self, run_id: str) -> RunOutcome:
        return self._port.run_outcome(run_id)

    def live(self, repo: str, pr: int) -> LiveHead:
        return self._port.live(repo, pr)

    def arm(self, repo: str, pr: int, head: str, *, holds: frozenset[tuple[str, int]]) -> ArmOutcome:
        return self._port.arm_head(repo, pr, head, holds=holds)

    def post_findings(self, repo: str, pr: int, head: str, run_id: str, text: str) -> None:
        self._port.post_findings(repo, pr, head, run_id, text)

    def escalate(self, repo: str, pr: int, head: str, reason: str, detail: str) -> None:
        self._port.send_escalation(repo, pr, head, reason, detail)
