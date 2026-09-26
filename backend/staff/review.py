"""Code Reviewer runtime: cross-provider selection, verdict parsing (WP-1.3, #1518)."""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from staff.verification import GhCliPrProbe

log = logging.getLogger("dashboard.staff.review")

DEFAULT_REVIEW_FOCUS = "code review"
DEFAULT_REVIEWER_PROVIDERS: tuple[str, ...] = ("claude", "codex", "gemini", "antigravity")
REVIEWER_ROLE = "code-reviewer"
AUTO_REVIEW_REQUESTER = "staff:auto-review"
# Carried in the review prompt so the runner, which only sees the run record, knows the
# verdict came from the author's own provider family (#1579).
SAME_PROVIDER_TAG = "[review:same-provider]"
# How many recent runs the author and dedup lookups scan.
RUN_LOOKUP_LIMIT = 200

PROVIDER_FAMILY: dict[str, str] = {
    "claude": "anthropic",
    "claude-ollama": "anthropic",
    "codex": "openai",
    "gemini": "google",
    "antigravity": "google",
    "cursor-agent": "cursor",
    "ollama": "local",
    "grok-chat": "xai",
}

ALTERNATE_MODELS: dict[str, str] = {
    "claude": "claude-3-5-haiku-20241022",
    "codex": "gpt-4o-mini",
    "gemini": "gemini-1.5-flash",
    "antigravity": "flash",
    "cursor-agent": "cursor-small",
    "ollama": "llama3.1:8b",
}

# The P0 and P1 repos from config/project_priorities.yaml
P0_P1_REPOS: frozenset[str] = frozenset(
    {
        "Gasification_Model",
        "Runner_Dashboard",
        "Tools",
        "UpstreamDrift",
        "AffineDrift",
        "Repository_Management",
        "Tools_Private",
    }
)

FORBIDDEN_REVIEW_KEYS: frozenset[str] = frozenset(
    {
        "request_changes",
        "event",
        "blocking",
        "enforce",
    }
)

_VERDICT_RE = re.compile(
    r"STAFF_RESULT:\s*review\s+(approve|changes|escalate)\s+#(\d+)",
    re.IGNORECASE,
)

_TRAILER_RE = re.compile(
    r"Agent-Id:\s*([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)


class CommitTrailerProbe(Protocol):
    def get_commit_messages(self, repo: str, pr_number: int) -> list[str]: ...


class GhCliCommitProbe(GhCliPrProbe):
    """Reads a PR's commit messages with one scoped ``gh api`` REST call.

    Reuses ``GhCliPrProbe``'s CLI plumbing, so it works from the runner's plain threads.
    """

    def get_commit_messages(self, repo: str, pr_number: int) -> list[str]:
        full = repo if "/" in repo else f"{self.org}/{repo}"
        commits = self._api(f"/repos/{full}/pulls/{int(pr_number)}/commits?per_page=100") or []
        return [str((c.get("commit") or {}).get("message") or "") for c in commits]


@dataclass(frozen=True)
class ReviewSelection:
    provider: str
    model: str | None = None
    same_provider: bool = False


@dataclass(frozen=True)
class ReviewVerdict:
    verdict: str | None
    pr_number: int | None
    outcome: str
    status: str  # "succeeded" | "failed" | "needs_input"
    same_provider: bool = False
    error: str | None = None


def select_reviewer_provider(
    author_provider: str | None,
    role_providers: list[str] | None = None,
) -> ReviewSelection:
    """Pick a reviewer provider that differs in provider family from the author.

    If only one provider family is available, uses an alternate model and flags same-provider.
    If the author is unknown, uses the first listed provider in role_providers.
    """
    providers = list(role_providers or DEFAULT_REVIEWER_PROVIDERS)
    if not providers:
        return ReviewSelection(provider="claude", model=None, same_provider=False)

    if not author_provider:
        return ReviewSelection(provider=providers[0], model=None, same_provider=False)

    author_family = PROVIDER_FAMILY.get(author_provider, author_provider)
    for p in providers:
        p_family = PROVIDER_FAMILY.get(p, p)
        if p != author_provider and p_family != author_family:
            return ReviewSelection(provider=p, model=None, same_provider=False)

    # Only the author's provider or provider family is available
    fallback_provider = providers[0]
    alt_model = ALTERNATE_MODELS.get(fallback_provider, "default-alternate")
    return ReviewSelection(provider=fallback_provider, model=alt_model, same_provider=True)


def detect_author_provider(
    repo: str,
    pr_number: int,
    branch: str = "",
    store: Any = None,
    gh_probe: Any = None,
) -> str | None:
    """Detect the provider that created the PR from the run store or commit trailers.

    ``RunStore.list_runs`` has no repo filter, so runs are filtered by repo here.
    """
    if store is not None:
        try:
            for r in store.list_runs(limit=RUN_LOOKUP_LIMIT):
                if getattr(r, "repo", "") != repo or getattr(r, "role", "") == REVIEWER_ROLE:
                    continue
                if getattr(r, "pr_number", None) == pr_number or (branch and getattr(r, "branch", "") == branch):
                    prov = getattr(r, "provider", None)
                    if prov:
                        return str(prov)
        except Exception:
            log.warning("Failed to lookup run by PR #%s in repo %s", pr_number, repo, exc_info=True)

    if gh_probe is not None:
        try:
            messages = gh_probe.get_commit_messages(repo, pr_number)
            for msg in messages:
                m = _TRAILER_RE.search(msg)
                if m:
                    return m.group(1).lower()
        except Exception:
            log.warning("Failed to inspect commit trailers for PR #%s in repo %s", pr_number, repo, exc_info=True)

    return None


def is_same_provider_review(prompt: str) -> bool:
    """Whether a review run's prompt carries the same-provider mark set at selection."""
    return SAME_PROVIDER_TAG in (prompt or "")


def parse_review_verdict(text: str, *, same_provider: bool = False) -> ReviewVerdict:
    """Parse a review verdict from STAFF_RESULT line.

    Valid: STAFF_RESULT: review approve|changes|escalate #<pr>
    Malformed: Line exists but fails to match contract -> status="failed"
    Missing: No STAFF_RESULT line -> status="needs_input"
    """
    if not text or "STAFF_RESULT:" not in text:
        return ReviewVerdict(verdict=None, pr_number=None, outcome="", status="needs_input")

    match = _VERDICT_RE.search(text)
    if match is not None:
        verdict = match.group(1).lower()
        pr = int(match.group(2))
        suffix = " (same-provider)" if same_provider else ""
        outcome = f"review {verdict} #{pr}{suffix}"
        return ReviewVerdict(
            verdict=verdict,
            pr_number=pr,
            outcome=outcome,
            status="succeeded",
            same_provider=same_provider,
        )

    return ReviewVerdict(
        verdict=None,
        pr_number=None,
        outcome="",
        status="failed",
        error="Malformed review verdict: expected 'STAFF_RESULT: review approve|changes|escalate #<pr>'",
    )


def parse_outcome(text: str, *, same_provider: bool = False) -> str:
    """Extract queryable outcome string for a review run; empty if absent or invalid."""
    res = parse_review_verdict(text, same_provider=same_provider)
    return res.outcome if res.status == "succeeded" else ""


def _role_providers(roster: Mapping[str, Any] | None, role: str) -> list[str] | None:
    spec = roster.get(role) if roster is not None else None
    providers = getattr(spec, "providers", None)
    return list(providers) if providers else None


def prepare_review_params(
    params: dict[str, Any],
    default_role: str = REVIEWER_ROLE,
    roster: Mapping[str, Any] | None = None,
    store: Any = None,
    gh_probe: CommitTrailerProbe | None = None,
) -> dict[str, Any]:
    """Validate, filter and enrich parameters for staff.review_pr dispatch.

    Pre: ``params["pr"]``, when present, is an integer PR number (callers validate it).
    Post: ``provider`` is set. The reviewer comes from the role's ``providers`` in
    ``roster`` and differs in family from the author found in ``store`` or in the PR's
    ``Agent-Id`` trailers via ``gh_probe``; a same-family fallback tags the prompt with
    ``SAME_PROVIDER_TAG``.
    """
    clean_params = {k: v for k, v in params.items() if k not in FORBIDDEN_REVIEW_KEYS}

    role = str(clean_params.get("reviewer") or default_role)
    if roster is not None and role not in roster:
        log.warning("Role '%s' not loaded; falling back to fleet-critic", role)
        role = "fleet-critic"

    repo = str(clean_params.get("repo") or "").strip()
    pr = clean_params.get("pr")
    focus = str(clean_params.get("focus") or DEFAULT_REVIEW_FOCUS)
    clean_params["role"] = role
    prompt = (
        f"Review PR #{pr} in {repo}. Focus: {focus}. "
        f"Advisory only: post a comment review ending in 'STAFF_RESULT: review approve|changes|escalate #{pr}'. "
        "Never request changes or block."
    )

    if not clean_params.get("provider"):
        author = (
            detect_author_provider(repo=repo, pr_number=int(pr), store=store, gh_probe=gh_probe)
            if pr and repo
            else None
        )
        sel = select_reviewer_provider(author, _role_providers(roster, role))
        clean_params["provider"] = sel.provider
        if sel.model:
            clean_params["model"] = sel.model
        if sel.same_provider:
            prompt = f"{prompt} {SAME_PROVIDER_TAG}"

    clean_params["prompt"] = prompt
    return clean_params


# Serialises the dedupe check with the submit that persists the queued run, so two
# verifications of one PR (a worker finish and a recheck) cannot both pass the check.
# The thread lock covers one process; ``_review_claim`` adds an ``flock`` on a file next
# to the shared runs DB so uvicorn workers (``WORKERS > 1``) are serialised too.
_AUTO_REVIEW_LOCK = threading.Lock()


@contextmanager
def _review_claim(store: Any) -> Iterator[None]:
    """Hold the auto-review claim across threads and, where ``fcntl`` exists, processes."""
    with _AUTO_REVIEW_LOCK:
        db_path = getattr(store, "path", None)
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows has no fcntl; one process there
            fcntl = None  # type: ignore[assignment]
        if fcntl is None or db_path is None:
            yield
            return
        with Path(f"{db_path}.auto-review.lock").open("w") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _already_reviewed(store: Any, repo: str, pr_number: int, role: str = REVIEWER_ROLE) -> bool:
    """Whether a ``role`` review run for this PR exists; ``role`` is the resolved reviewer."""
    target = f"PR #{pr_number}"
    return any(r.repo == repo and r.target_ref == target for r in store.list_runs(limit=RUN_LOOKUP_LIMIT, role=role))


def auto_review_if_eligible(
    rec: Any,
    verdict: Any,
    *,
    store: Any = None,
    runner: Any = None,
    gh_probe: CommitTrailerProbe | None = None,
) -> bool:
    """Submit an advisory code review when a run's PR reaches 'verified' on a P0/P1 repo.

    Opt-in via ``STAFF_AUTO_REVIEW``. Submits through ``runner.submit``, the thread-safe
    path the scheduler uses, because verification runs on plain threads where the
    event-loop bridge is unavailable (#1579). A PR that already has a code-reviewer run
    is skipped, so ``recheck_runs`` re-verifying it never queues a second review.
    Post: returns True only when a review run was queued; never raises.
    """
    setting = os.environ.get("STAFF_AUTO_REVIEW", "0").strip().lower()
    if setting not in ("1", "true", "yes"):
        return False

    if getattr(verdict, "verification", None) != "verified":
        return False

    pr_number = getattr(verdict, "pr_number", None)
    if not pr_number:
        return False

    if getattr(rec, "role", "") == REVIEWER_ROLE:
        return False

    repo = getattr(rec, "repo", "")
    if repo not in P0_P1_REPOS:
        return False

    try:
        if runner is None:
            from staff.runner import get_runner

            runner = get_runner()
        store = store if store is not None else runner.store
        params = prepare_review_params(
            {"repo": repo, "pr": int(pr_number)},
            roster=runner.roles(),
            store=store,
            gh_probe=gh_probe if gh_probe is not None else GhCliCommitProbe(),
        )
        from staff.plan import RunRequest

        reviewer = params["role"]  # code-reviewer, or its fleet-critic fallback
        with _review_claim(store):
            if _already_reviewed(store, repo, int(pr_number), role=reviewer):
                log.info("auto-review: PR #%s on %s already has a %s run; skipped", pr_number, repo, reviewer)
                return False
            run = runner.submit(
                RunRequest(
                    role=reviewer,
                    provider=params["provider"],
                    model=params.get("model"),
                    repo=repo,
                    pr=int(pr_number),
                    prompt=params["prompt"],
                    requested_by=AUTO_REVIEW_REQUESTER,
                    thread_id=getattr(rec, "thread_id", "") or "",
                )
            )
    except Exception as exc:  # noqa: BLE001 - auto-review must never break verification
        log.warning("auto-review: PR #%s on %s not dispatched: %s", pr_number, repo, exc, exc_info=True)
        return False
    log.info("auto-review: queued %s for PR #%s on %s with %s", run.id, pr_number, repo, params["provider"])
    return True
