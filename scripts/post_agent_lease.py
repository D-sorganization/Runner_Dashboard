"""CLI wrapper that posts an agent lease on an issue (#4083).

Ensures the fleet-App identity gate is enforced on the Python subprocess path.
Fails closed with exit code 3 (EXIT_IDENTITY_REFUSED) if the resolved gh
transport is not an active fleet-App bot account or resolves a personal login.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

EXIT_IDENTITY_REFUSED = 3
IDENTITY_REFUSED_MARKER = "claim-identity-refused"

_BOT_LOGIN = re.compile(r"^d-sorg[a-z0-9-]*agent\[bot\]$")
_LOGGED_IN = re.compile(r"Logged in to \S+ account (\S+)")
_ACTIVE = re.compile(r"Active account:\s*true")


def active_gh_account(status_text: str) -> str:
    """Return the active account from ``gh auth status`` output, or ``""``."""
    current = ""
    for line in status_text.splitlines():
        if line.strip().startswith("- Token:"):
            continue
        match = _LOGGED_IN.search(line)
        if match:
            current = match.group(1)
        elif "Failed to log in" in line:
            current = ""
        elif _ACTIVE.search(line) and current:
            return current
    return ""


def resolve_fleet_gh(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve the fleet-App gh wrapper binary path with fail-closed precedence."""
    e = dict(os.environ if env is None else env)
    if "FLEET_GH_BIN" in e:
        candidate = Path(e["FLEET_GH_BIN"]).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    if "FLEET_GH_WRAPPER_DIR" in e:
        candidate = Path(e["FLEET_GH_WRAPPER_DIR"]).expanduser() / "gh"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    for candidate in (
        Path.home() / ".local" / "bin" / "gh",
        Path.home() / ".claude" / "fleet-gh" / "bin" / "gh",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    path = e.get("PATH")
    found = shutil.which("gh", path=path)
    if found:
        return found
    return None


def _exec_gh(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
        env=dict(os.environ if env is None else env),
    )


def _refuse(agent: str, reason: str) -> int:
    err = f"{IDENTITY_REFUSED_MARKER}: {reason}"
    logger.error("post_agent_lease: identity refused: %s", err)
    payload = {
        "ok": False,
        "agent": agent,
        "identity_refused": True,
        "errors": [err],
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    return EXIT_IDENTITY_REFUSED


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--agent", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--intent", default="implement")
    p.add_argument("--ttl-hours", type=float, default=2.0)
    p.add_argument("--org", default="D-sorganization")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args(argv)

    if any("\r" in s or "\n" in s for s in (args.session, args.intent)):
        payload = {"ok": False, "agent": args.agent, "errors": ["invalid input: line breaks forbidden"]}
        sys.stdout.write(json.dumps(payload) + "\n")
        return 2

    gh_bin = resolve_fleet_gh()
    if not gh_bin:
        return _refuse(args.agent, "gh wrapper not found on PATH or fleet wrapper locations")

    env = dict(os.environ)
    env.setdefault("FLEET_GH_APP", "general")

    # Step 1: Verify auth status active account is fleet-App bot
    status = _exec_gh([gh_bin, "auth", "status"], env=env)
    login = active_gh_account(f"{status.stdout}\n{status.stderr}")
    if not _BOT_LOGIN.match(login):
        return _refuse(args.agent, f"active gh account {login or '<none>'} is not a fleet App bot")

    # Step 2: Verify gh api user fails (personal identity must NOT be active)
    user = _exec_gh([gh_bin, "api", "user"], env=env)
    if user.returncode == 0:
        return _refuse(
            args.agent,
            f"gh api user returned {user.stdout.strip() or '<empty>'}: a personal identity is active on this transport",
        )

    # Step 3: Verified fleet-App identity — proceed with claim posting
    full_repo = f"{args.org}/{args.repo}" if "/" not in args.repo else args.repo
    expires_at = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=args.ttl_hours)).isoformat()
    comment_body = (
        f"<!-- lease:post_agent_lease -->\n"
        f"lease:\n"
        f"  agent: {args.agent}\n"
        f"  session: {args.session}\n"
        f"  expires: {expires_at}\n"
        f"  intent: {args.intent}\n"
    )

    c_res = _exec_gh(
        [gh_bin, "issue", "comment", str(args.issue), "--repo", full_repo, "--body", comment_body], env=env
    )
    l_res = _exec_gh(
        [gh_bin, "issue", "edit", str(args.issue), "--repo", full_repo, "--add-label", f"claim:{args.agent}"], env=env
    )

    ok = c_res.returncode == 0
    receipt = c_res.stdout.strip() or (f"https://github.com/{full_repo}/issues/{args.issue}#lease" if ok else "")
    errors = []
    if c_res.returncode != 0:
        errors.append(c_res.stderr.strip() or "comment failed")
    if l_res.returncode != 0:
        errors.append(l_res.stderr.strip() or "label failed")

    payload = {
        "ok": ok,
        "agent": args.agent,
        "comment_posted": c_res.returncode == 0,
        "label_updated": l_res.returncode == 0,
        "receipt": receipt,
        "expires_at": expires_at,
        "errors": errors,
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
