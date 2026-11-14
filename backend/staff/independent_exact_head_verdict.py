"""Post independent exact-head verdict PR comments as the Runner Dashboard GitHub App (#4323).

MIRRORED CONTRACT. ``INDEPENDENT_REVIEW_MARKER`` and ``head_sha_matches`` (the
latter via ``coordination.merge_shepherd_exact_head``) are backend copies of
``shared_scripts/independent_exact_head_review.py``: the deploy artifact and
``python backend/server.py`` do not put the repo root (which holds
``shared_scripts``) on ``sys.path``, so importing it here crashed the
code-reviewer finalizer with ``No module named 'shared_scripts'`` (#4359).
``tests/unit/test_independent_exact_head_verdict_import.py`` and
``deploy/staff_code_reviewer_finalize_probe.py`` (wired through
``deploy/artifact_backend_import_smoke.py``) pin both copies to the originals
and block regressions at package time.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx
from coordination.merge_shepherd_exact_head import head_sha_matches
from dashboard_config import ORG
from github_app_auth import github_app_auth_configured, mint_installation_access_token
from security import validate_repo_slug
from staff.github_run_env import StaffGithubToken

log = logging.getLogger("dashboard.staff.independent_exact_head_verdict")

INDEPENDENT_REVIEW_MARKER = "Independent Exact-Head Acceptance Review"

VERDICT_COMMENT_PERMISSIONS = {
    "contents": "read",
    "issues": "write",
    "pull_requests": "write",
    "metadata": "read",
}

_STAFF_RESULT_INDEPENDENT_RE = re.compile(
    r"STAFF_RESULT:\s*independent_review\s+(?P<verdict>PASS|FIX-NEEDED)\s+#(?P<pr>\d+)\s+@(?P<sha>[0-9a-fA-F]{6,40})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IndependentExactHeadStaffResult:
    verdict: str
    pr_number: int
    head_sha: str


class VerdictPostError(RuntimeError):
    """Raised when the hub cannot post a verdict comment (fail loud, never silent)."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class VerdictContextMismatch(ValueError):
    """Parsed STAFF_RESULT does not match the trusted code-reviewer run context."""


def parse_independent_exact_head_staff_result(text: str) -> IndependentExactHeadStaffResult | None:
    if not text:
        return None
    for line in reversed(text.splitlines()):
        match = _STAFF_RESULT_INDEPENDENT_RE.search(line.strip())
        if match is None:
            continue
        return IndependentExactHeadStaffResult(
            verdict=match.group("verdict").upper(),
            pr_number=int(match.group("pr")),
            head_sha=match.group("sha").lower(),
        )
    return None


def validate_independent_verdict_against_run(
    parsed: IndependentExactHeadStaffResult,
    *,
    expected_pr_number: int,
    expected_head_sha: str,
) -> None:
    """Reject verdict lines that target a different PR or head than this run (#4324)."""
    if int(parsed.pr_number) != int(expected_pr_number):
        raise VerdictContextMismatch(
            f"STAFF_RESULT targets PR #{parsed.pr_number}, but this review run is for PR #{expected_pr_number}"
        )
    head = str(expected_head_sha or "").strip().lower()
    if not head:
        raise VerdictContextMismatch("PR head SHA is unknown; cannot bind independent verdict")
    if not head_sha_matches(parsed.head_sha, head):
        raise VerdictContextMismatch(f"STAFF_RESULT head @{parsed.head_sha} does not match the PR head @{head}")


def fetch_pull_head_sha(repo: str, pr_number: int) -> str:
    """Live PR head at post time, via the Runner Dashboard App installation."""
    slug = _repo_slug(repo)
    full_repo = f"{ORG}/{slug}"
    cred = mint_runner_dashboard_verdict_token(repo)
    url = f"https://api.github.com/repos/{full_repo}/pulls/{int(pr_number)}"
    try:
        resp = httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {cred.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(30.0),
        )
    except httpx.HTTPError as exc:
        raise VerdictPostError(f"GitHub PR lookup failed: {exc}") from exc
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:240]
        raise VerdictPostError(
            f"GitHub PR lookup refused: HTTP {resp.status_code} ({detail})",
            http_status=resp.status_code,
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise VerdictPostError("GitHub PR lookup returned non-JSON") from exc
    if not isinstance(payload, dict):
        raise VerdictPostError("GitHub PR lookup returned unexpected payload")
    head = payload.get("head")
    if not isinstance(head, dict):
        raise VerdictPostError("GitHub PR lookup missing head")
    sha = str(head.get("sha") or "").strip().lower()
    if not sha:
        raise VerdictPostError("GitHub PR lookup returned empty head.sha")
    return sha


def _repo_slug(repo: str) -> str:
    raw = str(repo or "").strip()
    if not raw:
        raise VerdictPostError("repository name is required for verdict post token mint")
    owner, separator, name = raw.partition("/")
    if separator and owner != ORG:
        raise VerdictPostError("repository outside configured organization scope")
    return validate_repo_slug(name if separator else raw)


def mint_runner_dashboard_verdict_token(repo: str) -> StaffGithubToken:
    """Mint via the hub's existing GitHub App installation path (d-sorg-runner-dashboard)."""
    if not github_app_auth_configured():
        raise VerdictPostError("GitHub App is not configured on the hub")
    slug = _repo_slug(repo)
    try:
        minted = mint_installation_access_token(
            repositories=[slug],
            permissions=VERDICT_COMMENT_PERMISSIONS,
        )
    except Exception as exc:
        raise VerdictPostError(f"Runner Dashboard App token mint failed: {exc}") from exc
    token = str(minted.token or "").strip()
    if not token:
        raise VerdictPostError("Runner Dashboard App token mint returned an empty token")
    return StaffGithubToken(token=token, expires_at=float(minted.expires_at), source="github_app")


def build_independent_verdict_comment_body(
    *,
    verdict: str,
    head_sha: str,
    reviewer_model: str,
    seat: str = "code-reviewer",
) -> str:
    v = verdict.upper()
    sha = head_sha.lower()
    model = reviewer_model.strip() or "unknown"
    return (
        f"### {INDEPENDENT_REVIEW_MARKER} (`{seat}` / `{model}`) — "
        f"Verdict: `{v} @{sha}`\n\n"
        f"- **Verdict:** **`{v} @{sha}`**\n"
    )


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def post_pr_comment(*, full_repo: str, pr_number: int, body: str, token: str) -> int:
    """Post one PR comment and return its id. Post: the id is a positive integer."""
    url = f"https://api.github.com/repos/{full_repo}/issues/{int(pr_number)}/comments"
    try:
        resp = httpx.post(
            url,
            json={"body": body},
            headers=_github_headers(token),
            timeout=httpx.Timeout(30.0),
        )
    except httpx.HTTPError as exc:
        raise VerdictPostError(f"GitHub comment post failed: {exc}") from exc
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:240]
        raise VerdictPostError(
            f"GitHub comment post refused: HTTP {resp.status_code} ({detail})",
            http_status=resp.status_code,
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise VerdictPostError("GitHub comment post returned non-JSON; the comment cannot be read back") from exc
    comment_id = payload.get("id") if isinstance(payload, dict) else None
    if isinstance(comment_id, bool) or not isinstance(comment_id, int) or comment_id <= 0:
        raise VerdictPostError("GitHub comment post returned no comment id; the comment cannot be read back")
    return comment_id


def confirm_verdict_comment_published(
    *, full_repo: str, comment_id: int, verdict: str, head_sha: str, token: str
) -> None:
    """Read the posted verdict comment back from GitHub (#4249, board ruling 2026-11-13).

    A post that GitHub answered with success is not yet a verdict: the head stays
    unreviewed until the comment is read back and names ``verdict`` at ``head_sha``.
    Raises :class:`VerdictPostError` otherwise.
    """
    url = f"https://api.github.com/repos/{full_repo}/issues/comments/{int(comment_id)}"
    try:
        resp = httpx.get(url, headers=_github_headers(token), timeout=httpx.Timeout(30.0))
    except httpx.HTTPError as exc:
        raise VerdictPostError(f"Verdict comment read-back failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VerdictPostError(
            f"Verdict comment read-back refused: HTTP {resp.status_code}; the head stays unreviewed",
            http_status=resp.status_code,
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise VerdictPostError("Verdict comment read-back returned non-JSON") from exc
    body = str(payload.get("body") or "") if isinstance(payload, dict) else ""
    expected = f"`{verdict.upper()} @{head_sha.lower()}`"
    if INDEPENDENT_REVIEW_MARKER not in body or expected not in body:
        raise VerdictPostError(f"Verdict comment read-back does not show {expected}; the head stays unreviewed")


def publish_independent_exact_head_verdict(
    *,
    repo: str,
    pr_number: int,
    verdict: str,
    head_sha: str,
    reviewer_model: str,
) -> None:
    """Publish one exact-head verdict and read it back; raises :class:`VerdictPostError` on failure.

    For ``PASS`` the formal APPROVE review goes first, so a refused review (for example
    HTTP 403) leaves no PASS comment on the PR. Post: the verdict comment, and for
    ``PASS`` the APPROVE review, were read back from GitHub at ``head_sha`` (#4249).
    """
    slug = _repo_slug(repo)
    full_repo = f"{ORG}/{slug}"
    cred = mint_runner_dashboard_verdict_token(repo)
    body = build_independent_verdict_comment_body(
        verdict=verdict,
        head_sha=head_sha,
        reviewer_model=reviewer_model,
    )
    log.info(
        "Posting independent exact-head verdict %s for %s#%s at %s",
        verdict.upper(),
        full_repo,
        pr_number,
        head_sha[:7],
    )
    if verdict.upper() == "PASS":
        from staff.staff_github_pr_review import ensure_runner_dashboard_formal_approve  # noqa: PLC0415

        ensure_runner_dashboard_formal_approve(
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            body=body,
        )
    comment_id = post_pr_comment(full_repo=full_repo, pr_number=pr_number, body=body, token=cred.token)
    confirm_verdict_comment_published(
        full_repo=full_repo,
        comment_id=comment_id,
        verdict=verdict,
        head_sha=head_sha,
        token=cred.token,
    )
