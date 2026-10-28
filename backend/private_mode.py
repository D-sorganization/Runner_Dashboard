"""Private Mode classification levels (issue #3250, epic #3249/#3077).

Defines the total-ordered :class:`Level` enum and the ``private_mode``
overlay schema field (#3079). An overlay with no ``private_mode`` key, or no
overlay at all, resolves to :data:`DISABLED` (``enabled=False``,
``default_level=Level.PUBLIC``): classification never diverges from today's
PUBLIC-everywhere behaviour. That is the safe-disable contract this slice
ships; every later Private Mode slice builds on it without changing it.

Level semantics:

* ``PUBLIC`` -- may appear on any surface: hub logs, transcripts, panel
  threads, board posts, metrics, GitHub logs and artifacts. Today's only
  level; unchanged while the flag is off.
* ``INTERNAL`` -- fleet-internal. Stays off public surfaces (public repo
  mirrors, public GitHub issues/PRs/comments, OSS artifacts) but may still
  reach the fleet's own private hub logs, transcripts and board. The level
  for private-but-not-sensitive content; a later slice (PM-1) migrates
  legacy, pre-flag threads here rather than leaving them PUBLIC.
* ``SENSITIVE`` -- must never reach *any* shared surface at all: not hub
  logs, not transcripts, not panel threads, not board posts, not metrics,
  not GitHub logs or artifacts. Confined to private, ephemeral, RD-owned
  infrastructure built out in later slices of this epic.

``Level`` is a total order (``PUBLIC < INTERNAL < SENSITIVE``): joining two
levels for the same content always keeps the more restrictive one.

Validation error messages name keys and reasons only; they never echo the
rejected value, because an operator-supplied ``default_level`` may be a
placeholder for something private.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

_FIELDS: dict[str, type] = {"enabled": bool, "default_level": str}
_POLICY_FIELDS: dict[str, type] = {
    "default_level": str,
    "repos": list,
    "path_globs": list,
    "operator_marks": dict,
}
POLICY_FILENAME = "classification.json"


class ClassificationError(RuntimeError):
    """Missing or invalid classification policy, or refused downgrade attempt."""


class Level(IntEnum):
    """A total order of classification levels, least to most restricted."""

    PUBLIC = 0
    INTERNAL = 1
    SENSITIVE = 2


@dataclass(frozen=True)
class PrivateModeConfig:
    """Validated ``private_mode`` overlay field. Construct via :func:`parse_private_mode`."""

    enabled: bool
    default_level: Level


DISABLED = PrivateModeConfig(enabled=False, default_level=Level.PUBLIC)


@dataclass(frozen=True)
class PathGlobRule:
    """A single path-glob classification rule in the private policy."""

    glob: str
    level: Level


@dataclass(frozen=True)
class ClassificationPolicy:
    """Validated classification policy loaded from the overlay's ``policy_dir``."""

    default_level: Level
    repos: Mapping[str, Level]
    path_globs: tuple[PathGlobRule, ...]
    operator_marks: Mapping[str, Level]


@dataclass(frozen=True)
class ClassificationInput:
    """Inputs to the pure classification resolver (:func:`resolve`)."""

    repo: str | None = None
    paths: tuple[str, ...] = ()
    target_id: str | None = None
    operator_mark: Level | None = None
    prior_level: Level | None = None
    is_legacy_thread: bool = False


def _parse_level(raw: object, where: str, *, err_cls: type[Exception] = ValueError) -> Level:
    if not isinstance(raw, str):
        raise err_cls(f"{where}: level must be of type str")
    try:
        return Level[raw]
    except KeyError:
        valid = ", ".join(member.name for member in Level)
        raise err_cls(f"{where}: unsupported level (expected one of {valid})") from None


def parse_private_mode(data: object | None) -> PrivateModeConfig:
    """Validate an optional ``private_mode`` overlay value.

    ``None`` (the key is absent) returns :data:`DISABLED` -- the
    safe-disable contract. Present, it must be an object with exactly
    ``enabled`` (bool) and ``default_level`` (one of :class:`Level`'s names).
    """
    if data is None:
        return DISABLED
    if not isinstance(data, dict):
        raise ValueError("private_mode: must be an object")
    missing = sorted(set(_FIELDS) - set(data))
    unknown = sorted(set(data) - set(_FIELDS))
    if missing:
        raise ValueError(f"private_mode: missing required key(s): {', '.join(missing)}")
    if unknown:
        raise ValueError(f"private_mode: unknown key(s): {', '.join(unknown)}")
    enabled = data["enabled"]
    if not isinstance(enabled, bool):
        raise ValueError("private_mode: key 'enabled' must be of type bool")
    level_name = data["default_level"]
    if not isinstance(level_name, str):
        raise ValueError("private_mode: key 'default_level' must be of type str")
    level = _parse_level(level_name, "private_mode")
    return PrivateModeConfig(enabled=enabled, default_level=level)


def parse_classification_policy(data: object) -> ClassificationPolicy:
    """Validate decoded classification policy JSON (issue #3251, PM-1).

    Fails closed with :class:`ClassificationError` without echoing private values.
    An unknown-repo ``default_level`` of ``PUBLIC`` is rejected so unknown
    repos always resolve to at least ``INTERNAL``.
    """
    if not isinstance(data, dict):
        raise ClassificationError("classification_policy: top level must be an object")
    missing = sorted(set(_POLICY_FIELDS) - set(data))
    unknown = sorted(set(data) - set(_POLICY_FIELDS))
    if missing:
        raise ClassificationError(f"classification_policy: missing required key(s): {', '.join(missing)}")
    if unknown:
        raise ClassificationError(f"classification_policy: unknown key(s): {', '.join(unknown)}")
    for key, expected in _POLICY_FIELDS.items():
        if isinstance(data[key], bool) or not isinstance(data[key], expected):
            raise ClassificationError(f"classification_policy: key '{key}' must be of type {expected.__name__}")
    default_level = _parse_level(
        data["default_level"], "classification_policy.default_level", err_cls=ClassificationError
    )
    if default_level < Level.INTERNAL:
        raise ClassificationError("classification_policy: default_level must be at least INTERNAL")
    repos: dict[str, Level] = {}
    for i, item in enumerate(data["repos"]):
        where = f"classification_policy.repos[{i}]"
        if not isinstance(item, dict) or set(item) != {"repo", "level"}:
            raise ClassificationError(f"{where}: must be an object with keys 'repo' and 'level'")
        slug = item["repo"]
        if not isinstance(slug, str) or not slug.strip():
            raise ClassificationError(f"{where}: key 'repo' must be a non-empty str")
        if slug in repos:
            raise ClassificationError(f"{where}: duplicate repo entry")
        repos[slug] = _parse_level(item["level"], where, err_cls=ClassificationError)
    globs: list[PathGlobRule] = []
    for i, item in enumerate(data["path_globs"]):
        where = f"classification_policy.path_globs[{i}]"
        if not isinstance(item, dict) or set(item) != {"glob", "level"}:
            raise ClassificationError(f"{where}: must be an object with keys 'glob' and 'level'")
        pattern = item["glob"]
        if not isinstance(pattern, str) or not pattern.strip():
            raise ClassificationError(f"{where}: key 'glob' must be a non-empty str")
        globs.append(PathGlobRule(glob=pattern, level=_parse_level(item["level"], where, err_cls=ClassificationError)))
    marks: dict[str, Level] = {}
    for target, raw_level in data["operator_marks"].items():
        if not isinstance(target, str) or not target.strip():
            raise ClassificationError("classification_policy.operator_marks: keys must be non-empty str")
        marks[target] = _parse_level(raw_level, "classification_policy.operator_marks", err_cls=ClassificationError)
    return ClassificationPolicy(
        default_level=default_level,
        repos=repos,
        path_globs=tuple(globs),
        operator_marks=marks,
    )


def load_classification_policy(path: Path) -> ClassificationPolicy:
    """Read and validate the classification policy file at *path* (no path echo)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ClassificationError("classification_policy: file does not exist") from None
    except OSError:
        raise ClassificationError("classification_policy: file is not readable") from None
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        raise ClassificationError("classification_policy: file is not valid JSON") from None
    return parse_classification_policy(data)


def load_overlay_classification_policy(overlay: Any, *, base_dir: Path | None = None) -> ClassificationPolicy:
    """Load the classification policy from a validated :class:`Overlay`'s ``policy_dir``.

    Fails closed with :class:`ClassificationError` when *overlay* is missing or
    invalid, or when ``<policy_dir>/classification.json`` is missing or invalid.
    """
    policy_dir = getattr(overlay, "policy_dir", None)
    if not isinstance(policy_dir, str) or not policy_dir.strip():
        raise ClassificationError("classification_policy: overlay is missing a valid policy_dir")
    raw_dir = Path(policy_dir).expanduser()
    if not raw_dir.is_absolute() and base_dir is not None:
        raw_dir = Path(base_dir) / raw_dir
    return load_classification_policy(raw_dir / POLICY_FILENAME)


def max_join(*levels: Level) -> Level:
    """Least upper bound on the :class:`Level` lattice (PUBLIC < INTERNAL < SENSITIVE)."""
    if not levels:
        return Level.PUBLIC
    for lvl in levels:
        if not isinstance(lvl, Level):
            raise ClassificationError("max_join: all arguments must be Level instances")
    return max(levels)


def _path_matches_glob(rel_path: str, pattern: str) -> bool:
    norm_path = rel_path.strip("/")
    norm_pat = pattern.strip("/")
    if PurePosixPath(norm_path).match(norm_pat) or fnmatchcase(norm_path, norm_pat):
        return True
    if norm_pat.endswith("/**"):
        prefix = norm_pat[:-3].rstrip("/")
        if norm_path == prefix or norm_path.startswith(prefix + "/"):
            return True
    return False


def resolve(
    inputs: ClassificationInput,
    *,
    config: PrivateModeConfig = DISABLED,
    policy: ClassificationPolicy | None = None,
) -> Level:
    """Pure classification resolver (issue #3251, PM-1).

    Preconditions (DbC):
    * *inputs* is a :class:`ClassificationInput` and *config* is a :class:`PrivateModeConfig`.
    * When ``config.enabled`` is ``False``, returns ``Level.PUBLIC`` (flag-off invariant).
    * When ``config.enabled`` is ``True``, a validated *policy* is required; missing
      policy raises :class:`ClassificationError` (blocks private execution).
    * Downgrade attempts (where computed classification is strictly below
      ``inputs.prior_level``, or an explicit ``operator_mark`` tries to lower the
      repo/path/prior level) raise :class:`ClassificationError`.
    """
    if not isinstance(inputs, ClassificationInput):
        raise ClassificationError("resolve: inputs must be a ClassificationInput")
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("resolve: config must be a PrivateModeConfig")
    if not config.enabled:
        return Level.PUBLIC
    if not isinstance(policy, ClassificationPolicy):
        raise ClassificationError("resolve: validated classification policy is required when private_mode is enabled")

    base_unknown = max_join(policy.default_level, config.default_level, Level.INTERNAL)
    if inputs.repo and inputs.repo in policy.repos:
        repo_level = policy.repos[inputs.repo]
    else:
        repo_level = base_unknown

    level = repo_level
    for rel_path in inputs.paths:
        for rule in policy.path_globs:
            if _path_matches_glob(rel_path, rule.glob):
                level = max_join(level, rule.level)

    if inputs.is_legacy_thread:
        level = max_join(level, Level.INTERNAL)

    if inputs.target_id and inputs.target_id in policy.operator_marks:
        mark_level = policy.operator_marks[inputs.target_id]
        if mark_level < level:
            raise ClassificationError("resolve: operator mark cannot downgrade classification level")
        level = max_join(level, mark_level)

    if inputs.operator_mark is not None:
        if not isinstance(inputs.operator_mark, Level):
            raise ClassificationError("resolve: operator_mark must be a Level")
        if inputs.operator_mark < level or (
            inputs.prior_level is not None and inputs.operator_mark < inputs.prior_level
        ):
            raise ClassificationError("resolve: operator_mark cannot downgrade classification level")
        level = max_join(level, inputs.operator_mark)

    if inputs.prior_level is not None:
        if not isinstance(inputs.prior_level, Level):
            raise ClassificationError("resolve: prior_level must be a Level")
        if level < inputs.prior_level:
            raise ClassificationError("resolve: classification downgrade is refused without audited declassify")

    return level
