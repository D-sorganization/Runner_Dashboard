"""Post independent exact-head verdict PR comments as the Runner Dashboard GitHub App (#4323)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx
from dashboard_config import ORG
from github_app_auth import github_app_auth_configured, mint_installation_access_token
from security import validate_repo_slug
from staff.github_run_env import StaffGithubToken

from shared_scripts.independent_exact_head_review import head_sha_matches

log = logging.getLogger("dashboard.staff.independent_exact_head_verdict")

VERDICT_COMMENT_PERMISSIONS = {
    "contents": "read",
    "issues": "write",
    "pull_requests": "read",
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
    from shared_scripts.independent_exact_head_review import INDEPENDENT_REVIEW_MARKER

    v = verdict.upper()
    sha = head_sha.lower()
    model = reviewer_model.strip() or "unknown"
    return (
        f"### {INDEPENDENT_REVIEW_MARKER} (`{seat}` / `{model}`) — "
        f"Verdict: `{v} @{sha}`\n\n"
        f"- **Verdict:** **`{v} @{sha}`**\n"
    )


def post_pr_comment(*, full_repo: str, pr_number: int, body: str, token: str) -> None:
    url = f"https://api.github.com/repos/{full_repo}/issues/{int(pr_number)}/comments"
    try:
        resp = httpx.post(
            url,
            json={"body": body},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
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


def publish_independent_exact_head_verdict(
    *,
    repo: str,
    pr_number: int,
    verdict: str,
    head_sha: str,
    reviewer_model: str,
) -> None:
    """Post one exact-head verdict comment; raises :class:`VerdictPostError` on failure."""
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
    post_pr_comment(full_repo=full_repo, pr_number=pr_number, body=body, token=cred.token)
