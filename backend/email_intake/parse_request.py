"""Parse dispatch hints from a plain-text email body."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ROLE_RE = re.compile(r"^role:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_REPO_RE = re.compile(r"^repo:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_ISSUE_RE = re.compile(r"^issue:\s*(\d+)\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class DispatchHints:
    role: str
    repo: str
    issue: int | None
    prompt: str


def parse_dispatch_hints(body: str) -> DispatchHints | None:
    """Parse dispatch hints; returns None when the message omits ``role:``."""
    role_match = _ROLE_RE.search(body)
    if not role_match:
        return None
    repo_match = _REPO_RE.search(body)
    issue_match = _ISSUE_RE.search(body)
    role = role_match.group(1).strip().lower()
    repo = repo_match.group(1).strip() if repo_match else ""
    issue = int(issue_match.group(1)) if issue_match else None
    lines = []
    for line in body.splitlines():
        if re.match(r"^(role|repo|issue):\s*", line, re.IGNORECASE):
            continue
        lines.append(line)
    prompt = "\n".join(lines).strip() or body.strip()
    return DispatchHints(role=role, repo=repo, issue=issue, prompt=prompt)
