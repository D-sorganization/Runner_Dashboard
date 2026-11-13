"""Refuse ``gh pr create`` on hub-declared drain-mode repos (#3915 slice 2)."""

from __future__ import annotations

import logging
import os
import sys
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from coordination.drain_directive_gate import (
    classify_pr_create_exemption,
    normalize_repo_query,
    should_block_pr_create,
)

log = logging.getLogger("dashboard.staff.gh_drain_gate")

_DRAIN_PATH = "/api/directives/drain"
_FAIL_OPEN_WARNING = "WARNING [gh-drain-gate]: hub drain status unavailable; allowing gh pr create (fail-open)."


def parse_pr_create_args(argv: Sequence[str]) -> tuple[str | None, str | None, list[str]]:
    """Return ``(repo, title, labels)`` parsed from ``gh pr create`` argv."""
    if len(argv) < 2 or argv[0] != "pr" or argv[1] != "create":
        return None, None, []
    repo = title = None
    labels: list[str] = []
    i = 2
    while i < len(argv):
        tok = argv[i]
        if tok in ("-R", "--repo") and i + 1 < len(argv):
            repo = argv[i + 1]
            i += 2
        elif tok.startswith("--repo="):
            repo = tok.split("=", 1)[1]
            i += 1
        elif tok in ("-t", "--title") and i + 1 < len(argv):
            title = argv[i + 1]
            i += 2
        elif tok.startswith("--title="):
            title = tok.split("=", 1)[1]
            i += 1
        elif tok in ("-l", "--label") and i + 1 < len(argv):
            labels.append(argv[i + 1])
            i += 2
        elif tok.startswith("--label="):
            labels.append(tok.split("=", 1)[1])
            i += 1
        else:
            i += 1
    return repo, title, labels


def _hub_base(env: Mapping[str, str]) -> str:
    raw = (env.get("HUB_URL") or os.environ.get("HUB_URL") or "http://127.0.0.1:8321").strip().rstrip("/")
    return raw


def _fleet_token(env: Mapping[str, str]) -> str:
    return (env.get("HUB_FLEET_TOKEN") or env.get("FLEET_API_TOKEN") or os.environ.get("HUB_FLEET_TOKEN") or "").strip()


def fetch_hub_drain_status(repo: str, env: Mapping[str, str] | None = None) -> tuple[dict[str, Any] | None, bool]:
    """Query the hub drain endpoint. Returns ``(payload, hub_reachable)``."""
    env_map = os.environ if env is None else env
    try:
        _, full_repo = normalize_repo_query(repo)
    except ValueError:
        return None, False
    hub = _hub_base(env_map)
    token = _fleet_token(env_map)
    params = {"repo": full_repo}
    url = f"{hub}{_DRAIN_PATH}?{urllib.parse.urlencode(params)}"
    headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.get(url, headers=headers, timeout=httpx.Timeout(8.0))
    except httpx.HTTPError as exc:
        log.warning("hub drain status unreachable at %s: %s", url, exc)
        return None, False
    if resp.status_code >= 400:
        log.warning("hub drain status HTTP %s at %s: %s", resp.status_code, url, resp.text[:200])
        return None, False
    try:
        body = resp.json()
    except ValueError:
        return None, False
    return (body if isinstance(body, dict) else None), True


def check_drain_gate_for_pr_create(
    argv: Sequence[str],
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """Return ``(allowed, error_message)`` for ``gh pr create`` argv."""
    env_map = os.environ if env is None else env
    if (env_map.get("GH_BYPASS_DRAIN_GATE") or "").strip() == "1":
        return True, ""
    repo, title, labels = parse_pr_create_args(argv)
    if repo is None:
        return True, ""

    payload, hub_ok = fetch_hub_drain_status(repo, env=env_map)
    if not hub_ok or payload is None:
        sys.stderr.write(_FAIL_OPEN_WARNING + "\n")
        return True, ""

    drained = bool(payload.get("drained"))
    exempt = classify_pr_create_exemption(title=title or "", labels=labels)
    if should_block_pr_create(drained=drained, exempt=exempt):
        threshold = payload.get("threshold", 20)
        open_count = payload.get("open_pr_count", "?")
        expires = payload.get("expires") or "none"
        try:
            _, full_repo = normalize_repo_query(repo)
        except ValueError:
            full_repo = repo
        return (
            False,
            "ERROR [gh-drain-gate]: Refusing `gh pr create` while drain mode is active for "
            f"{full_repo} ({open_count} open PRs, threshold {threshold}, expires {expires}). "
            "Only ci/security/red-main (and related unblock) PRs are allowed until open PRs "
            "fall below the threshold or the directive expires.",
        )
    return True, ""
