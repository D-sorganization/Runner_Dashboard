"""Code Reviewer runtime: cross-provider selection, verdict parsing (WP-1.3, #1518)."""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("dashboard.staff.review")

DEFAULT_REVIEW_FOCUS = "code review"

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

# Appended to a review prompt when no other provider family was available; the runner
# reads it back so the stored outcome says ``(same-provider)`` (#1579).
SAME_PROVIDER_TAG = "[review:same-provider]"

# How many recent runs the author lookup scans (``RunStore.list_runs`` has no repo filter).
AUTHOR_LOOKUP_LIMIT = 500

_TRAILER_RE = re.compile(
    r"Agent-Id:\s*([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)


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
    providers = list(role_providers or ["claude", "codex", "gemini", "antigravity"])
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
    """Detect the provider that created the PR from the run store or commit trailers."""
    if store is not None:
        try:
            runs = store.list_runs(limit=AUTHOR_LOOKUP_LIMIT)
            for r in runs:
                if getattr(r, "role", "") == "code-reviewer" or not _same_repo(getattr(r, "repo", ""), repo):
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


def _same_repo(a: str, b: str) -> bool:
    """Whether two repo names match, ignoring an ``owner/`` prefix and case."""
    return bool(a and b) and a.rsplit("/", 1)[-1].lower() == b.rsplit("/", 1)[-1].lower()


def review_outcome(prompt: str, result_line: str) -> str:
    """The stored outcome for a finished run, flagged same-provider when its prompt says so (#1579)."""
    return parse_outcome(result_line, same_provider=SAME_PROVIDER_TAG in (prompt or ""))


def parse_outcome(text: str, *, same_provider: bool = False) -> str:
    """Extract queryable outcome string for a review run; empty if absent or invalid."""
    res = parse_review_verdict(text, same_provider=same_provider)
    return res.outcome if res.status == "succeeded" else ""


def prepare_review_params(
    params: dict[str, Any],
    default_role: str = "code-reviewer",
    roster: Mapping[str, Any] | None = None,
    store: Any = None,
    gh_probe: Any = None,
) -> dict[str, Any]:
    """Validate, filter and enrich parameters for staff.review_pr dispatch."""
    clean_params = {k: v for k, v in params.items() if k not in FORBIDDEN_REVIEW_KEYS}

    role = str(clean_params.get("reviewer") or default_role)
    if roster is not None and role not in roster:
        log.warning("Role '%s' not loaded; falling back to fleet-critic", role)
        role = "fleet-critic"

    repo = str(clean_params.get("repo") or "").strip()
    pr = clean_params.get("pr")
    focus = str(clean_params.get("focus") or DEFAULT_REVIEW_FOCUS)
    clean_params["role"] = role
    clean_params["prompt"] = (
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
        role_spec = roster.get(role) if roster is not None else None
        role_providers = [str(p) for p in getattr(role_spec, "providers", None) or []] or None
        sel = select_reviewer_provider(author, role_providers)
        clean_params["provider"] = sel.provider
        if sel.model:
            clean_params["model"] = sel.model
        if sel.same_provider:
            clean_params["prompt"] = f"{clean_params['prompt']} {SAME_PROVIDER_TAG}"

    return clean_params


# Serialises the dedupe check with the submit that persists the queued run, so two
# verifications of one PR cannot both pass the check (#1580 review). One process per node.
_AUTO_REVIEW_LOCK = threading.Lock()


def _already_reviewed(store: Any, repo: str, pr_number: int, role: str = "code-reviewer") -> bool:
    """Whether a ``role`` review run for this PR is already queued, running or done."""
    if store is None:
        return False
    target = f"PR #{pr_number}"
    for r in store.list_runs(role=role, limit=AUTHOR_LOOKUP_LIMIT):
        if (
            _same_repo(getattr(r, "repo", ""), repo)
            and getattr(r, "target_ref", "") == target
            and getattr(r, "status", "") not in ("failed", "cancelled")
        ):
            return True
    return False


def auto_review_if_eligible(
    rec: Any,
    verdict: Any,
    *,
    store: Any = None,
    dispatch_fn: Any = None,
    runner: Any = None,
    gh_probe: Any = None,
) -> bool:
    """Optionally start a code review when a run's PR reaches 'verified' on a P0/P1 repo.

    Called from the runner's and scheduler's plain threads, so it submits through
    ``Runner.submit`` (the scheduler's thread-safe path), not the event-loop bridge.
    Returns True only when a review was actually submitted; never raises.
    """
    setting = os.environ.get("STAFF_AUTO_REVIEW", "0").strip().lower()
    if setting not in ("1", "true", "yes"):
        return False

    if getattr(verdict, "verification", None) != "verified":
        return False

    pr_number = getattr(verdict, "pr_number", None)
    if not pr_number:
        return False

    role = getattr(rec, "role", "")
    if role == "code-reviewer":
        return False

    repo = getattr(rec, "repo", "")
    if repo not in P0_P1_REPOS:
        return False

    if dispatch_fn is not None:
        try:
            dispatch_fn({"repo": repo, "pr": pr_number, "reviewer": "code-reviewer"})
            return True
        except Exception:
            log.warning("Failed to auto-dispatch review for PR #%s on %s", pr_number, repo, exc_info=True)
            return False

    try:
        from staff.plan import RunRequest

        if runner is None:
            from staff.runner import get_runner

            runner = get_runner()
        roles = runner.roles() if callable(getattr(runner, "roles", None)) else None
        params = prepare_review_params(
            {"repo": repo, "pr": int(pr_number), "reviewer": "code-reviewer"},
            roster=roles if isinstance(roles, Mapping) else None,
            store=store,
            gh_probe=gh_probe,
        )
        reviewer = str(params["role"])  # code-reviewer, or its fleet-critic fallback
        with _AUTO_REVIEW_LOCK:
            if _already_reviewed(store, repo, int(pr_number), role=reviewer):
                log.info("auto-review: %s PR #%s already has a %s run; skipping", repo, pr_number, reviewer)
                return False
            run = runner.submit(
                RunRequest(
                    role=reviewer,
                    provider=params.get("provider"),
                    model=params.get("model"),
                    repo=repo,
                    pr=int(pr_number),
                    prompt=str(params["prompt"]),
                    requested_by="auto-review",
                )
            )
        log.info("auto-review: submitted %s for %s PR #%s", getattr(run, "id", "run"), repo, pr_number)
        return True
    except Exception:
        log.warning("Failed to auto-dispatch review for PR #%s on %s", pr_number, repo, exc_info=True)
        return False
