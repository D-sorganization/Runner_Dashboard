"""Read-only, warn-only preflight for the prospective per-agent PR cap."""

from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit

from scripts.pr_cap_check import (
    PR_CAP,
    count_open,
    eligible_open_prs,
    render_summary,
    verdict,
)
from scripts.pr_cap_report import fetch_open_prs, prepare_report_data
from shared_scripts.agent_identity import (
    normalize_pr_agent_id,
    pr_head_branch,
    pr_repository,
    resolve_pr_agent,
)

_RUNNER = Callable[..., subprocess.CompletedProcess[str]]
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SCP_ORIGIN_RE = re.compile(r"^git@github\.com:([^/]+)/(.+)$", re.IGNORECASE)
_WARN_TEXT = {
    "api_error": "WARN: GitHub API request failed; cap was not evaluated.",
    "api_timeout": "WARN: GitHub API request timed out; cap was not evaluated.",
    "invalid_metadata": (
        "WARN: GitHub returned incomplete or invalid pull-request metadata; "
        "cap was not evaluated."
    ),
}


def prepare_preflight(repo_root: Path, *, run: _RUNNER = subprocess.run) -> str:
    """Prepare a sanitized report for the current branch without gating."""
    git_metadata = _current_git_metadata(repo_root, run)
    if git_metadata is None:
        return "WARN: Git metadata unavailable; cap was not evaluated."
    branch, repository = git_metadata

    open_prs, error = fetch_open_prs(repository, run=run)
    if error is not None:
        return _WARN_TEXT[error]

    current = _existing_own_pr(open_prs, branch, repository)
    if current is not None:
        report = prepare_report_data(repository, _number(current), run=run)
        return _current_pr_text(report)

    agent = resolve_pr_agent({"headRefName": branch})
    eligible = eligible_open_prs(open_prs)
    unknown_count = sum(
        1
        for pr in eligible
        if (identity := resolve_pr_agent(pr)) is None or identity == "user"
    )
    percentage = unknown_count / len(eligible) * 100 if eligible else 0.0
    if agent is None or agent == "user":
        return (
            "WARN: current branch identity is unknown; cap was not evaluated. "
            f"Unknown identity share: {unknown_count}/{len(eligible)} "
            f"({percentage:.1f}%)."
        )

    identity = normalize_pr_agent_id(agent)
    current_count = count_open(eligible, identity)
    matching_numbers = sorted(
        _number(pr) for pr in eligible if resolve_pr_agent(pr) == identity
    )
    candidate: dict[str, object] = {
        "headRefName": branch,
        "state": "open",
        "repository": repository,
    }
    decision = verdict(open_prs, candidate)
    matching = ", ".join(f"#{number}" for number in matching_numbers) or "none"
    if decision == "FAIL":
        heading = (
            f"WARN: opening a PR would exceed the cap ({current_count}/{PR_CAP} "
            f"already open for {identity}); matching PRs {matching}."
        )
    elif decision == "PASS":
        heading = (
            f"PASS: opening a PR would use {current_count + 1}/{PR_CAP} "
            f"eligible open PRs for {identity}; matching PRs {matching}."
        )
    else:
        heading = "WARN: PR-cap policy could not evaluate this branch."
    return (
        f"{heading} Unknown identity share: {unknown_count}/{len(eligible)} "
        f"({percentage:.1f}%)."
    )


def _current_git_metadata(repo_root: Path, run: _RUNNER) -> tuple[str, str] | None:
    try:
        branch_result = _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"], run)
        origin_result = _run_git(repo_root, ["remote", "get-url", "origin"], run)
    except (OSError, subprocess.TimeoutExpired):
        return None
    branch = branch_result.stdout.strip()
    repository = _github_repository(origin_result.stdout.strip())
    if not branch or branch == "HEAD" or repository is None:
        return None
    return branch, repository


def _run_git(
    repo_root: Path, arguments: list[str], run: _RUNNER
) -> subprocess.CompletedProcess[str]:
    response = run(
        ["git", "-C", str(repo_root), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if not isinstance(response, subprocess.CompletedProcess) or response.returncode:
        raise OSError("git metadata unavailable")
    if not isinstance(response.stdout, str):
        raise OSError("git metadata unavailable")
    return response


def _github_repository(origin: str) -> str | None:
    scp_match = _SCP_ORIGIN_RE.fullmatch(origin)
    if scp_match:
        path = scp_match.group(1) + "/" + scp_match.group(2)
    else:
        try:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"https", "ssh"}:
                return None
            if parsed.hostname != "github.com" or parsed.password is not None:
                return None
            if parsed.scheme == "https" and parsed.username is not None:
                return None
            if parsed.scheme == "ssh" and parsed.username not in {None, "git"}:
                return None
            if parsed.query or parsed.fragment:
                return None
            path = parsed.path.lstrip("/")
        except ValueError:
            return None
    if path.endswith(".git"):
        path = path[:-4]
    if not _REPOSITORY_RE.fullmatch(path):
        return None
    if any(part in {".", ".."} for part in path.split("/")):
        return None
    return path


def _existing_own_pr(
    prs: Sequence[Mapping[str, object]], branch: str, repository: str
) -> Mapping[str, object] | None:
    for pr in prs:
        if (
            pr_head_branch(pr) == branch
            and (head_repository := _head_repository(pr)) is not None
            and head_repository.casefold() == repository.casefold()
            and (target_repository := pr_repository(pr)) is not None
            and target_repository.casefold() == repository.casefold()
        ):
            return pr
    return None


def _head_repository(pr: Mapping[str, object]) -> str | None:
    head = pr.get("head")
    head_repo = head.get("repo") if isinstance(head, Mapping) else None
    full_name = head_repo.get("full_name") if isinstance(head_repo, Mapping) else None
    return full_name if isinstance(full_name, str) else None


def _current_pr_text(report: Mapping[str, object]) -> str:
    return f"current PR is already open; {render_summary(report)}"


def _number(pr: Mapping[str, object]) -> int:
    number = pr.get("number")
    if type(number) is not int or number <= 0:
        raise ValueError("invalid PR number")
    return number


def _absolute_repo_root(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("must be an absolute repository path")
    return path


def run_preflight(
    repo_root: Path | None = None, *, run: _RUNNER = subprocess.run
) -> None:
    """Print the fail-open preflight summary."""
    print(prepare_preflight(repo_root or Path.cwd(), run=run))


def main(argv: Sequence[str] | None = None, *, run: _RUNNER = subprocess.run) -> int:
    """Run the preflight CLI; valid invocations always return success."""
    parser = argparse.ArgumentParser(
        description="Warn about the per-agent open PR cap."
    )
    parser.add_argument("--repo-root", required=True, type=_absolute_repo_root)
    args = parser.parse_args(argv)
    run_preflight(args.repo_root, run=run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
