"""Detect drift between RD's vendored Maxwell OpenAPI snapshot and MD upstream."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_UPSTREAM_REPO = "D-sorganization/Maxwell_Daemon"
DEFAULT_UPSTREAM_REF = "main"
DEFAULT_UPSTREAM_PATH = "docs/reference/openapi.json"
DEFAULT_ISSUE_TITLE = "Maxwell OpenAPI contract drift detected"


def _read_json_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fetch_url(url: str, *, token: str | None = None) -> str:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _raw_github_url(repo: str, ref: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"


def _canonical_json(text: str) -> str:
    return json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"


def snapshots_match(vendored_text: str, upstream_text: str) -> bool:
    return _canonical_json(vendored_text) == _canonical_json(upstream_text)


def _github_api(method: str, url: str, *, token: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _workflow_url() -> str | None:
    server = os.getenv("GITHUB_SERVER_URL")
    repo = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if not (server and repo and run_id):
        return None
    return f"{server}/{repo}/actions/runs/{run_id}"


def report_drift_issue(
    *,
    issue_repo: str,
    token: str,
    title: str,
    upstream_label: str,
    vendored_path: Path,
) -> str:
    api_root = f"https://api.github.com/repos/{issue_repo}"
    existing = _github_api("GET", f"{api_root}/issues?state=open&per_page=100", token=token)
    body_lines = [
        "The vendored Maxwell OpenAPI snapshot no longer matches the upstream Maxwell_Daemon snapshot.",
        "",
        f"- Vendored snapshot: `{vendored_path.as_posix()}`",
        f"- Upstream snapshot: `{upstream_label}`",
    ]
    workflow_url = _workflow_url()
    if workflow_url:
        body_lines.append(f"- Workflow run: {workflow_url}")
    body_lines.extend(
        [
            "",
            "Refresh the vendored snapshot from Maxwell_Daemon and rerun `tests/test_maxwell_contract.py`.",
        ]
    )
    body = "\n".join(body_lines)

    for issue in existing:
        if issue.get("title") == title:
            _github_api("POST", issue["comments_url"], token=token, payload={"body": body})
            return str(issue["html_url"])

    created = _github_api(
        "POST",
        f"{api_root}/issues",
        token=token,
        payload={"title": title, "body": body},
    )
    return str(created["html_url"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendored", default="tests/contracts/maxwell_openapi.json")
    parser.add_argument("--upstream-file")
    parser.add_argument("--upstream-url")
    parser.add_argument("--upstream-repo", default=DEFAULT_UPSTREAM_REPO)
    parser.add_argument("--upstream-ref", default=DEFAULT_UPSTREAM_REF)
    parser.add_argument("--upstream-path", default=DEFAULT_UPSTREAM_PATH)
    parser.add_argument("--issue-repo")
    parser.add_argument("--issue-title", default=DEFAULT_ISSUE_TITLE)
    args = parser.parse_args(argv)

    vendored_path = Path(args.vendored)
    vendored = _read_json_text(vendored_path)
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")

    if args.upstream_file:
        upstream_label = args.upstream_file
        upstream = _read_json_text(Path(args.upstream_file))
    else:
        upstream_url = args.upstream_url or _raw_github_url(args.upstream_repo, args.upstream_ref, args.upstream_path)
        upstream_label = upstream_url
        upstream = _fetch_url(upstream_url, token=token)

    if snapshots_match(vendored, upstream):
        print("Vendored Maxwell OpenAPI snapshot matches upstream.")
        return 0

    print("Vendored Maxwell OpenAPI snapshot differs from upstream.", file=sys.stderr)
    if args.issue_repo and token:
        try:
            issue_url = report_drift_issue(
                issue_repo=args.issue_repo,
                token=token,
                title=args.issue_title,
                upstream_label=upstream_label,
                vendored_path=vendored_path,
            )
            print(f"Drift issue recorded: {issue_url}", file=sys.stderr)
        except urllib.error.HTTPError as exc:
            print(f"Failed to record drift issue: HTTP {exc.code}", file=sys.stderr)
    elif args.issue_repo:
        print("No GH_TOKEN/GITHUB_TOKEN set; cannot record drift issue.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
