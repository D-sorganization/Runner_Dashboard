"""Decide whether pull_request Docker image validation should run (docker-build.yml).

When the PR files API is unavailable (403 without ``pull-requests: read``, network,
etc.), fail open to full Docker validation so CI does not deadlock (#2764).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

_PREFIXES: tuple[str, ...] = (
    "backend/",
    "frontend/",
    "config/",
    "docker/",
)
_EXACT: frozenset[str] = frozenset(
    {
        "Dockerfile",
        "requirements.lock.txt",
        "requirements.txt",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/docker-build.yml",
    }
)


def should_run_docker_for_files(files: list[str]) -> bool:
    """True when changed paths warrant a Docker image build on a pull request."""
    if len(files) >= 100:
        return True
    return any(
        path.startswith(_PREFIXES)
        or path in _EXACT
        or (path.startswith("Dockerfile") and path != "Dockerfile.sample")
        for path in files
    )


def fetch_pr_changed_files(repo: str, pr_number: str, token: str) -> list[str] | None:
    """Return PR file paths, or None when the API call fails (fail-open upstream)."""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files?per_page=100"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, list):
        return None
    return [str(item.get("filename", "")) for item in payload if item.get("filename")]


def decide_run_docker_for_pr(repo: str, pr_number: str, token: str) -> bool:
    """Fail open to full Docker validation when the files API cannot be read."""
    files = fetch_pr_changed_files(repo, pr_number, token)
    if files is None:
        return True
    return should_run_docker_for_files(files)


def main() -> int:
    repo = os.environ.get("REPOSITORY", "")
    pr_number = os.environ.get("PR_NUMBER", "")
    token = os.environ.get("GH_TOKEN", "")
    if not repo or not pr_number or not token:
        print("run_docker=true", file=sys.stdout)
        print("docker_scope=missing_env_fail_open", file=sys.stderr)
        return 0
    files = fetch_pr_changed_files(repo, pr_number, token)
    if files is None:
        print("run_docker=true", file=sys.stdout)
        print("docker_scope=api_unavailable_fail_open", file=sys.stderr)
        return 0
    run = should_run_docker_for_files(files)
    print(f"run_docker={'true' if run else 'false'}", file=sys.stdout)
    print(f"changed_file_count={len(files)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
