"""Dispatch-time hold checks for scoped fleet policies (#2657).

Reuses :class:`staff.holds.HoldsList` and :func:`staff.holds.matches` — the same
decision primitives the scheduler uses — but applies guardrail holds when a
dispatch slice matches a hold's scope (deploy/release work on Runner_Dashboard).
"""

from __future__ import annotations

import fnmatch
import re
from typing import TYPE_CHECKING

from dispatch_routing import extract_declared_paths
from staff.exclusions import normalize_repo as normalize_repo_name
from staff.holds import RD_DEPLOY_RELEASE_HOLD_PREFIX, matches

if TYPE_CHECKING:
    from staff.holds import Hold, HoldsList

_AUTO_RELEASE_FIX_PATHS = frozenset(
    {
        ".github/workflows/main-ci-auto-release.yml",
        ".github/workflows/ci-auto-deploy-regression.yml",
    }
)
_AUTO_RELEASE_FIX_PREFIXES = (
    "deploy/node_auto_deploy/",
    "deploy/node-auto-deploy",
)
_DEPLOY_RELEASE_PATH_PREFIXES = (
    "deploy/",
    "dashboard-releases/",
)
_DEPLOY_RELEASE_PATH_GLOBS = (
    ".github/workflows/main-ci-auto-release.yml",
    ".github/workflows/deploy-qualified-release.yml",
    ".github/workflows/release.yml",
    ".github/workflows/ci-auto-deploy-regression.yml",
)
_DEPLOY_RELEASE_PROMPT_RE = re.compile(
    r"\b(auto[- ]release|dashboard-releases?|release packaging|deploy[-/ ]release)\b",
    re.IGNORECASE,
)


def is_rd_deploy_release_hold(hold: Hold) -> bool:
    return hold.text.strip().startswith(RD_DEPLOY_RELEASE_HOLD_PREFIX)


def _path_is_deploy_release(path: str) -> bool:
    norm = path.strip().replace("\\", "/").lstrip("/")
    if not norm or norm == "*":
        return False
    for prefix in _DEPLOY_RELEASE_PATH_PREFIXES:
        if norm.startswith(prefix):
            return True
    for pattern in _DEPLOY_RELEASE_PATH_GLOBS:
        if fnmatch.fnmatch(norm, pattern):
            return True
    if "dashboard-releases" in norm:
        return True
    return False


def is_deploy_release_slice(repo: str, prompt: str, paths: list[str] | None = None) -> bool:
    """True when dispatch targets Runner_Dashboard deploy or release feature work."""
    if normalize_repo_name(repo) != "runner_dashboard":
        return False
    declared = paths if paths is not None else extract_declared_paths(prompt)
    if any(_path_is_deploy_release(p) for p in declared):
        return True
    return bool(prompt and _DEPLOY_RELEASE_PROMPT_RE.search(prompt))


def _path_allowed_for_auto_release_fix(path: str) -> bool:
    norm = path.strip().replace("\\", "/").lstrip("/")
    if norm in _AUTO_RELEASE_FIX_PATHS:
        return True
    return any(norm.startswith(prefix) for prefix in _AUTO_RELEASE_FIX_PREFIXES)


def is_auto_release_fix(repo: str, prompt: str, paths: list[str] | None = None) -> bool:
    """Bug fixes scoped to the auto-release workflow itself stay dispatchable under the hold."""
    if normalize_repo_name(repo) != "runner_dashboard":
        return False
    if not prompt or not re.search(r"\bauto[- ]release\b", prompt, re.IGNORECASE):
        return False
    declared = paths if paths is not None else extract_declared_paths(prompt)
    if not declared or declared == ["*"]:
        return True
    touched = {p.strip().replace("\\", "/").lstrip("/") for p in declared if p.strip()}
    if not touched:
        return True
    return all(_path_allowed_for_auto_release_fix(p) for p in touched)


def blocking_dispatch_hold(
    holds: HoldsList,
    role: str,
    repo: str,
    prompt: str,
) -> Hold | None:
    """First active hold that blocks this dispatch slice, or None when dispatch may proceed."""
    if not repo:
        return None
    paths = extract_declared_paths(prompt)
    for hold in holds.load():
        if not hold.active or not is_rd_deploy_release_hold(hold):
            continue
        if not matches(hold, role, repo):
            continue
        if not is_deploy_release_slice(repo, prompt, paths):
            continue
        if is_auto_release_fix(repo, prompt, paths):
            continue
        return hold
    return None
