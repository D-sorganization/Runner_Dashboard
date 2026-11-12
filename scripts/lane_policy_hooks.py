#!/usr/bin/env python3
"""Default fleet-policy hooks for a lane profile (#3933 S3, #3963 S2).

Two ``pre_tool`` checks, blocking by default, enforce the fleet's standing
git rules before a lane runs the command they guard:

* ``no_verify_guard`` -- never ``--no-verify`` or ``SKIP=<hook>``.
* ``no_force_push_guard`` -- never a bare ``--force``/``-f`` push
  (``--force-with-lease`` is a different flag and is allowed).

One ``pre_tool`` advisory hook (non-blocking):

* ``diff_size_guard`` -- populates ``changed_lines`` from the real diff via
  :func:`scripts.pr_size_gate.evaluate_size` when ``repo_root`` is set, or
  accepts a caller-supplied ``changed_lines`` int. Over the reference cap it
  only logs a warning — line count is not a merge or push blocker (#3963).

One ``on_start`` hook, non-blocking, bootstraps the worktree-private
pre-push gate:

* ``install_pre_push_hook_guard`` -- installs a ``pre-push`` git hook (via
  :func:`install_lane_pre_push_hook`) that shells out to
  ``scripts/pr_size_gate.py`` directly, structurally mirroring
  ``backend/staff/git_hooks.py::install_pre_push_hook``'s worktree-private
  ``core.hooksPath`` pattern -- but as fleet-lane-side code for CLI agent
  lanes (claude/codex/cursor/agy/gemini), not the Staff Hub product
  worktrees that file serves.

:func:`apply_default_policy_hooks` prepends these to a
:class:`scripts.lane_hooks.LaneProfile`'s declared hooks at each point
without disturbing any hooks the profile already declares at any point.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.lane_hooks import HookSpec, LaneProfile
from scripts.pr_size_gate import DEFAULT_ALLOWLIST_NAME, DEFAULT_LIMIT, evaluate_size

logger = logging.getLogger(__name__)

_NO_VERIFY_CALLABLE = "scripts.lane_policy_hooks:no_verify_guard"
_NO_FORCE_PUSH_CALLABLE = "scripts.lane_policy_hooks:no_force_push_guard"
_DIFF_SIZE_CALLABLE = "scripts.lane_policy_hooks:diff_size_guard"
_INSTALL_PRE_PUSH_HOOK_CALLABLE = "scripts.lane_policy_hooks:install_pre_push_hook_guard"

#: Single source of truth for the cap; re-exported name kept for callers
#: that still read the "500" figure off this module directly.
_MAX_CHANGED_LINES = DEFAULT_LIMIT
_SKIP_ENV_PATTERN = re.compile(r"\bSKIP=")
_BARE_FORCE_FLAG_PATTERN = re.compile(r"(?:^|\s)(--force|-f)(?:\s|$)")


class PolicyViolation(RuntimeError):
    """Raised when a default fleet-policy hook's check fails."""


def no_verify_guard(context: dict[str, Any]) -> None:
    """Block any command that bypasses hooks via ``--no-verify`` or ``SKIP=``."""
    command = context.get("command", "")
    if not isinstance(command, str) or not command:
        return
    if "--no-verify" in command:
        raise PolicyViolation("command uses --no-verify, which is never allowed")
    if _SKIP_ENV_PATTERN.search(command):
        raise PolicyViolation("command sets SKIP=, which is never allowed")


def no_force_push_guard(context: dict[str, Any]) -> None:
    """Block a bare ``git push --force``/``-f``; ``--force-with-lease`` is fine."""
    command = context.get("command", "")
    if not isinstance(command, str) or not command:
        return
    if "push" not in command:
        return
    if _BARE_FORCE_FLAG_PATTERN.search(command):
        raise PolicyViolation("command is a bare force-push (use --force-with-lease, or don't force)")


def diff_size_guard(context: dict[str, Any]) -> None:
    """Advisory: populate ``changed_lines`` and warn when over the reference cap.

    When ``context["repo_root"]`` is set, the real diff is counted via
    :func:`scripts.pr_size_gate.evaluate_size` (same counter and allowlist as
    ``scripts/pr_size_gate.py``) and written to ``context["changed_lines"]``.
    Without ``repo_root``, a pre-populated ``changed_lines`` int is used for
    tests and hermetic callers.

    Never raises for size alone — prefer small cohesive PRs; do not split a
    cohesive fix only to hit a line count.
    """
    repo_root = context.get("repo_root")
    if repo_root is not None:
        repo_root = Path(repo_root)
        allowlist_path = Path(context.get("allowlist_path", repo_root / DEFAULT_ALLOWLIST_NAME))
        kwargs: dict[str, Any] = {}
        if "run" in context:
            kwargs["run"] = context["run"]
        verdict = evaluate_size(
            repo_root,
            merge_base=context.get("merge_base", "origin/main"),
            limit=context.get("limit", DEFAULT_LIMIT),
            pr_number=context.get("pr_number"),
            allowlist_path=allowlist_path,
            **kwargs,
        )
        context["changed_lines"] = verdict.total_lines
        context["pr_size_over_reference_cap"] = not verdict.within_limit
        if not verdict.within_limit:
            logger.warning(
                "PR size advisory: %s changed lines (reference cap %s). "
                "Prefer small, cohesive PRs; do not split a cohesive fix only to hit a line count.",
                verdict.total_lines,
                context.get("limit", DEFAULT_LIMIT),
            )
        return

    changed_lines = context.get("changed_lines")
    if changed_lines is None:
        return
    if not isinstance(changed_lines, int):
        raise PolicyViolation("changed_lines must be an int")
    context["pr_size_over_reference_cap"] = changed_lines > _MAX_CHANGED_LINES
    if changed_lines > _MAX_CHANGED_LINES:
        logger.warning(
            "PR size advisory: %s changed lines (reference cap %s). "
            "Prefer small, cohesive PRs; do not split a cohesive fix only to hit a line count.",
            changed_lines,
            _MAX_CHANGED_LINES,
        )


def install_lane_pre_push_hook(worktree: Path) -> Path:
    """Install a worktree-private ``pre-push`` hook that runs ``pr_size_gate.py``.

    Structurally mirrors ``backend/staff/git_hooks.py::install_pre_push_hook``
    (a worktree-private hooks dir wired via ``git config --worktree
    core.hooksPath``) but is fleet-lane-side: the installed hook shells out
    to ``scripts/pr_size_gate.py`` directly as a subprocess rather than
    calling back into a Staff Hub Python entrypoint, since this serves CLI
    agent lanes (claude/codex/cursor/agy/gemini), not Staff Hub worktrees.
    """
    root = worktree.resolve(strict=True)
    git_dir = Path(
        subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve()
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-push"
    gate_script = root / "scripts" / "pr_size_gate.py"
    script = (
        f"#!/usr/bin/env sh\nset -eu\nexec {shlex.quote(sys.executable or 'python3')} "
        f"{shlex.quote(str(gate_script))} --repo-root {shlex.quote(str(root))} "
        "--merge-base origin/main\n"
    )
    hook.write_text(script, encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(
        ["git", "config", "--worktree", "core.hooksPath", str(hooks_dir)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return hook


def install_pre_push_hook_guard(context: dict[str, Any]) -> None:
    """``on_start`` hook: install the pre-push size gate into the lane's worktree.

    Non-blocking -- a lane that has no concrete worktree yet in its context
    (e.g. a hermetic test, or a hook point invoked before checkout) just
    skips installing rather than failing the lane's start-up.
    """
    worktree = context.get("worktree") or context.get("repo_root")
    if worktree is None:
        return
    install_lane_pre_push_hook(Path(worktree))


def default_policy_hooks() -> dict[str, tuple[HookSpec, ...]]:
    """The default fleet-policy hooks: one ``on_start`` bootstrap step, three ``pre_tool`` guards."""
    return {
        "on_start": (
            HookSpec(id="install-pre-push-hook", callable_path=_INSTALL_PRE_PUSH_HOOK_CALLABLE, blocking=False),
        ),
        "pre_tool": (
            HookSpec(id="no-verify-guard", callable_path=_NO_VERIFY_CALLABLE, blocking=True),
            HookSpec(id="no-force-push-guard", callable_path=_NO_FORCE_PUSH_CALLABLE, blocking=True),
            HookSpec(id="diff-size-guard", callable_path=_DIFF_SIZE_CALLABLE, blocking=False),
        ),
    }


def apply_default_policy_hooks(profile: LaneProfile) -> LaneProfile:
    """Return a copy of ``profile`` with the default policy hooks prepended.

    Any hooks the profile already declares (at ``pre_tool`` or any other
    point) are preserved and run after the defaults.
    """
    defaults = default_policy_hooks()
    merged_hooks = dict(profile.hooks)
    for point, specs in defaults.items():
        merged_hooks[point] = specs + merged_hooks.get(point, ())
    return LaneProfile(name=profile.name, hooks=merged_hooks)
