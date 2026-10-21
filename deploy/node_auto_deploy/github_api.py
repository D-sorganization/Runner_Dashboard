"""Minimal GitHub REST client for auto-deploy (uses node GH_TOKEN, not App keys)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class GitHubApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"GitHub API {status}: {detail}")
        self.status = status
        self.detail = detail


def load_token_from_env_file(path: str) -> str | None:
    try:
        from pathlib import Path

        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("GH_TOKEN="):
            token = line.split("=", 1)[1].strip()
            return token or None
    return None


def github_get(path: str, token: str, *, api_root: str = "https://api.github.com") -> Any:
    if not path.startswith("/"):
        raise ValueError("path must start with /")
    url = f"{api_root.rstrip('/')}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "runner-dashboard-node-auto-deploy",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise GitHubApiError(exc.code, detail) from exc
    return json.loads(body)
