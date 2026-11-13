"""Small synchronous ``gh api`` helper for coordination evidence lookups."""

from __future__ import annotations

import json
import subprocess  # noqa: S404
from typing import Any
from urllib.parse import quote

from coordination.rm_scripts import _rm_env
from github_pagination import gh_cli_paginate
from github_rate_governor import (
    GitHubGovernorExhausted,
    acquire_before_github_call,
    looks_rate_limited_output,
    parse_gh_cli_retry_after_seconds,
    record_github_rate_limit,
)

GH_TIMEOUT_SECONDS = 20


def gh_api_json_all(path: str) -> list[Any]:
    """Paginated list fetch for coordination evidence (all pages, ``per_page=100``)."""
    return gh_cli_paginate(path, env=_rm_env(), timeout=GH_TIMEOUT_SECONDS)


def gh_api_json(path: str) -> Any:
    try:
        acquire_before_github_call()
    except GitHubGovernorExhausted as exc:
        raise RuntimeError(f"github rate governor exhausted; retry after {int(exc.retry_after_seconds)}s") from exc
    proc = subprocess.run(  # noqa: S603
        ["gh", "api", path],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
        env=_rm_env(),
    )
    if proc.returncode != 0:
        output = "\n".join(part for part in (proc.stderr, proc.stdout) if part)
        if looks_rate_limited_output(output):
            retry_after = parse_gh_cli_retry_after_seconds(output)
            record_github_rate_limit(retry_after_seconds=retry_after)
        raise RuntimeError((proc.stderr or proc.stdout or f"gh api {path} failed").strip()[:300])
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError(f"gh api {path} returned empty stdout")
    return json.loads(out)


def remove_issue_label(repo_full: str, issue: int, label: str) -> None:
    """Remove ``label`` from an issue (``DELETE .../issues/{n}/labels/{label}``).

    Pre: ``repo_full`` is ``owner/name``; ``issue`` is positive; ``label`` is non-empty.
    Post: the label is not on the issue (GitHub's 404 "Label does not exist" counts).
    Raises ``RuntimeError`` on any other failure.
    """
    if int(issue) <= 0 or not label.strip() or repo_full.count("/") != 1:
        raise ValueError("remove_issue_label needs owner/name, a positive issue and a label")
    path = f"/repos/{repo_full}/issues/{int(issue)}/labels/{quote(label, safe='')}"
    try:
        acquire_before_github_call()
    except GitHubGovernorExhausted as exc:
        raise RuntimeError(f"github rate governor exhausted; retry after {int(exc.retry_after_seconds)}s") from exc
    proc = subprocess.run(  # noqa: S603
        ["gh", "api", "-X", "DELETE", path],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
        env=_rm_env(),
    )
    if proc.returncode == 0:
        return
    output = "\n".join(part for part in (proc.stderr, proc.stdout) if part)
    if "HTTP 404" in output and "Label does not exist" in output:
        return
    if looks_rate_limited_output(output):
        record_github_rate_limit(retry_after_seconds=parse_gh_cli_retry_after_seconds(output))
    raise RuntimeError((output or f"gh api DELETE {path} failed").strip()[:300])
