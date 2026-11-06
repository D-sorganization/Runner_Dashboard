#!/usr/bin/env python3
"""Lane hook runner and profile schema (#3933 S2).

A lane profile declares fleet-policy hooks at four points in a lane's
run: ``on_start``, ``pre_tool``, ``post_tool`` and ``on_stop``. Each hook
resolves to a Python callable (``"module:function"``) that receives a
context dict. A blocking hook (the default) that raises stops the
triggering action by raising :class:`HookBlockedError`; a non-blocking
hook's failure is recorded in the returned results but does not stop
anything. Every hook failure is logged via the standard ``logging``
module (no ``print()``, per this repo's conventions).

The schema is documented at ``docs/lanes/schema/lane-profile.schema.json``.
S1 (#3933) shipped the separate spec-card templates/validator; S3 adds the
default policy hooks (no ``--no-verify``, no bare force-push; a non-blocking
PR size advisory) on top of this runner.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HOOK_POINTS: tuple[str, ...] = ("on_start", "pre_tool", "post_tool", "on_stop")


class HookBlockedError(RuntimeError):
    """Raised when a blocking hook fails; the triggering action must stop."""

    def __init__(self, point: str, hook_id: str, reason: str) -> None:
        self.point = point
        self.hook_id = hook_id
        self.reason = reason
        super().__init__(f"hook {hook_id!r} blocked {point}: {reason}")


@dataclass(frozen=True)
class HookSpec:
    """One hook entry declared in a lane profile."""

    id: str
    callable_path: str
    blocking: bool = True


@dataclass(frozen=True)
class HookResult:
    """The outcome of running a single hook."""

    hook_id: str
    point: str
    ok: bool
    detail: str = ""


def _require_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"lane profile requires a non-empty '{what}'")
    return value


def _parse_hook_entry(point: str, entry: Any) -> HookSpec:
    if not isinstance(entry, dict):
        raise ValueError(f"hook entry in '{point}' must be a mapping")
    hook_id = _require_str(entry.get("id"), "id")
    callable_path = entry.get("callable")
    if not isinstance(callable_path, str) or ":" not in callable_path:
        raise ValueError(f"hook {hook_id!r} requires 'callable' as 'module:function'")
    return HookSpec(id=hook_id, callable_path=callable_path, blocking=bool(entry.get("blocking", True)))


@dataclass(frozen=True)
class LaneProfile:
    """A lane's declared identity and its hooks at each hook point."""

    name: str
    hooks: dict[str, tuple[HookSpec, ...]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LaneProfile:
        name = _require_str(data.get("name"), "name")
        hooks_data = data.get("hooks", {})
        if not isinstance(hooks_data, dict):
            raise ValueError("lane profile 'hooks' must be a mapping")
        unknown = sorted(set(hooks_data) - set(HOOK_POINTS))
        if unknown:
            raise ValueError(f"unknown hook point(s): {unknown}")

        hooks: dict[str, tuple[HookSpec, ...]] = {}
        for point in HOOK_POINTS:
            entries = hooks_data.get(point, [])
            if not isinstance(entries, list):
                raise ValueError(f"hooks for '{point}' must be a list")
            hooks[point] = tuple(_parse_hook_entry(point, entry) for entry in entries)
        return cls(name=name, hooks=hooks)

    @classmethod
    def load(cls, path: Path) -> LaneProfile:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)


def _resolve_callable(callable_path: str) -> Callable[[dict[str, Any]], None]:
    module_name, _, func_name = callable_path.partition(":")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, func_name)
    except AttributeError as exc:
        raise ValueError(f"{callable_path!r} has no attribute {func_name!r}") from exc


class HookRunner:
    """Runs a lane profile's hooks for a given point, in declared order."""

    def __init__(self, profile: LaneProfile) -> None:
        self._profile = profile

    def run(self, point: str, context: dict[str, Any]) -> list[HookResult]:
        if point not in HOOK_POINTS:
            raise ValueError(f"unknown hook point: {point!r}")

        results: list[HookResult] = []
        for spec in self._profile.hooks.get(point, ()):
            try:
                func = _resolve_callable(spec.callable_path)
                func(context)
            except Exception as exc:  # noqa: BLE001 - a hook failure is data, not a crash
                logger.error("lane hook %r at %r failed: %s", spec.id, point, exc)
                results.append(HookResult(hook_id=spec.id, point=point, ok=False, detail=str(exc)))
                if spec.blocking:
                    raise HookBlockedError(point, spec.id, str(exc)) from exc
            else:
                results.append(HookResult(hook_id=spec.id, point=point, ok=True))
        return results
