#!/usr/bin/env python3
"""Default fleet-policy hooks for a lane profile (#3933 S3).

Three ``pre_tool`` checks, blocking by default, enforce the fleet's
standing git/PR rules before a lane runs the command they guard:

* ``no_verify_guard`` -- never ``--no-verify`` or ``SKIP=<hook>``.
* ``no_force_push_guard`` -- never a bare ``--force``/``-f`` push
  (``--force-with-lease`` is a different flag and is allowed).
* ``diff_size_guard`` -- a PR's changed-line count stays at or under 500.

:func:`apply_default_policy_hooks` prepends these three to a
:class:`scripts.lane_hooks.LaneProfile`'s ``pre_tool`` hooks without
disturbing any hooks the profile already declares at any point.
"""

from __future__ import annotations

import re
from typing import Any

from scripts.lane_hooks import HookSpec, LaneProfile

_NO_VERIFY_CALLABLE = "scripts.lane_policy_hooks:no_verify_guard"
_NO_FORCE_PUSH_CALLABLE = "scripts.lane_policy_hooks:no_force_push_guard"
_DIFF_SIZE_CALLABLE = "scripts.lane_policy_hooks:diff_size_guard"

_MAX_CHANGED_LINES = 500
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
    """Block a PR whose changed-line count exceeds the 500-line cap."""
    changed_lines = context.get("changed_lines")
    if changed_lines is None:
        return
    if not isinstance(changed_lines, int):
        raise PolicyViolation("changed_lines must be an int")
    if changed_lines > _MAX_CHANGED_LINES:
        raise PolicyViolation(f"changed_lines={changed_lines} exceeds the {_MAX_CHANGED_LINES}-line cap")


def default_policy_hooks() -> dict[str, tuple[HookSpec, ...]]:
    """The default fleet-policy hooks, all blocking, all at ``pre_tool``."""
    return {
        "pre_tool": (
            HookSpec(id="no-verify-guard", callable_path=_NO_VERIFY_CALLABLE, blocking=True),
            HookSpec(id="no-force-push-guard", callable_path=_NO_FORCE_PUSH_CALLABLE, blocking=True),
            HookSpec(id="diff-size-guard", callable_path=_DIFF_SIZE_CALLABLE, blocking=True),
        )
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
