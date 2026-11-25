#!/usr/bin/env python3
"""Reject PR commits that use unallowlisted GitHub users.noreply addresses.

The check covers commits in ``base..head`` only. Merged history at or before
``base`` is out of scope. Non-noreply emails are ignored.

Preconditions: ``--base`` and ``--head`` resolve in ``--repo-root``; the
allowlist is a JSON object with an ``identities`` array of ``{id, login}``
strings. Postcondition: exit 0 when every author email, committer email, and
``Co-authored-by`` trailer in the range is either not a noreply address or an
allowlisted ``<id>+<login>@users.noreply.github.com``; otherwise exit 1 and
write one stderr line per violation.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NOREPLY_SUFFIX = "@users.noreply.github.com"
NOREPLY_WITH_ID = re.compile(r"^(\d+)\+(.+)@users\.noreply\.github\.com$")
CO_AUTHORED_BY = re.compile(
    r"^Co-authored-by:\s*.+\s*<([^>]+)>\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class IdentityViolation:
    commit: str
    field: str
    email: str
    reason: str


def load_allowlist(path: Path) -> set[tuple[str, str]]:
    """Preconditions: ``path`` is UTF-8 JSON with string ``id`` and ``login`` entries."""
    data = json.loads(path.read_text(encoding="utf-8"))
    identities = data.get("identities")
    if not isinstance(identities, list):
        msg = f"allowlist {path} must contain an 'identities' array"
        raise ValueError(msg)
    allow: set[tuple[str, str]] = set()
    for entry in identities:
        if not isinstance(entry, dict):
            msg = f"allowlist entry must be an object: {entry!r}"
            raise ValueError(msg)
        author_id = entry.get("id")
        login = entry.get("login")
        if not isinstance(author_id, str) or not isinstance(login, str):
            msg = f"allowlist entry must have string id and login: {entry!r}"
            raise ValueError(msg)
        if not author_id.isdigit() or not login:
            msg = f"allowlist id must be digits and login non-empty: {entry!r}"
            raise ValueError(msg)
        allow.add((author_id, login))
    if not allow:
        msg = f"allowlist {path} has no identities"
        raise ValueError(msg)
    return allow


def validate_noreply_email(email: str, allowlist: set[tuple[str, str]]) -> str | None:
    """Return a violation reason, or None if the address is allowed.

    Postcondition: None when ``email`` does not end with the GitHub noreply
    suffix, or when it is ``<id>+<login>@users.noreply.github.com`` and that
    pair is in ``allowlist``.
    """
    if not email.endswith(NOREPLY_SUFFIX):
        return None
    match = NOREPLY_WITH_ID.match(email)
    if not match:
        return f"noreply address must be <id>+<login>@users.noreply.github.com (got {email!r})"
    author_id, login = match.group(1), match.group(2)
    if (author_id, login) not in allowlist:
        return f"({author_id}, {login!r}) is not in the fleet noreply allowlist (address {email!r})"
    return None


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def commits_in_range(repo_root: Path, base: str, head: str) -> list[str]:
    """Commits reachable from ``head`` and not from ``base`` (``base..head``)."""
    if base == head:
        return []
    out = _git(repo_root, "rev-list", f"{base}..{head}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def noreply_violations_for_commit(
    repo_root: Path, commit: str, allowlist: set[tuple[str, str]]
) -> list[IdentityViolation]:
    violations: list[IdentityViolation] = []
    fields = (
        ("author email", "%ae"),
        ("committer email", "%ce"),
    )
    for field, fmt in fields:
        email = _git(repo_root, "show", "-s", f"--format={fmt}", commit).strip()
        reason = validate_noreply_email(email, allowlist)
        if reason:
            violations.append(IdentityViolation(commit=commit, field=field, email=email, reason=reason))
    body = _git(repo_root, "show", "-s", "--format=%B", commit)
    for match in CO_AUTHORED_BY.finditer(body):
        email = match.group(1).strip()
        reason = validate_noreply_email(email, allowlist)
        if reason:
            violations.append(
                IdentityViolation(
                    commit=commit,
                    field="Co-authored-by",
                    email=email,
                    reason=reason,
                )
            )
    return violations


def check_range(
    repo_root: Path,
    base: str,
    head: str,
    allowlist: set[tuple[str, str]],
) -> list[IdentityViolation]:
    violations: list[IdentityViolation] = []
    for commit in commits_in_range(repo_root, base, head):
        violations.extend(noreply_violations_for_commit(repo_root, commit, allowlist))
    return violations


def default_allowlist_path(repo_root: Path) -> Path:
    return repo_root / "config" / "fleet_noreply_identities.json"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Git repository root (default: .)",
    )
    parser.add_argument("--base", required=True, help="Base commit (exclusive range start)")
    parser.add_argument("--head", required=True, help="Head commit (inclusive range end)")
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=None,
        help="JSON allowlist path (default: config/fleet_noreply_identities.json)",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    allowlist_path = args.allowlist if args.allowlist is not None else default_allowlist_path(repo_root)
    allowlist = load_allowlist(allowlist_path)
    violations = check_range(repo_root, args.base, args.head, allowlist)
    if not violations:
        logger.info(
            "noreply identity check passed for %s..%s (%d commits scanned)",
            args.base[:7],
            args.head[:7],
            len(commits_in_range(repo_root, args.base, args.head)),
        )
        return 0
    for item in violations:
        short = item.commit[:7]
        print(
            f"commit {short} {item.field}: {item.email} — {item.reason}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
