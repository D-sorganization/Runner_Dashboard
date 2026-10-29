"""Private Mode classification levels (issue #3250, epic #3249/#3077).

Defines the total-ordered :class:`Level` enum (``PUBLIC < INTERNAL < SENSITIVE``)
and the ``private_mode`` overlay schema field (#3079). An overlay with no
``private_mode`` key, or no overlay at all, resolves to :data:`DISABLED`
(``enabled=False``, ``default_level=Level.PUBLIC``). Validation error messages
name keys and reasons only; they never echo rejected values.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

_FIELDS: dict[str, type] = {"enabled": bool, "default_level": str}
_POLICY_FIELDS: dict[str, type] = {
    "default_level": str,
    "repos": list,
    "path_globs": list,
    "operator_marks": dict,
}
POLICY_FILENAME = "classification.json"
ENVELOPE_SCHEMA_VERSION = 1
_T = TypeVar("_T")


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
    missing, unknown = sorted(set(_POLICY_FIELDS) - set(data)), sorted(set(data) - set(_POLICY_FIELDS))
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
    return ClassificationPolicy(default_level=default_level, repos=repos, path_globs=tuple(globs), operator_marks=marks)


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
    inputs: ClassificationInput, *, config: PrivateModeConfig = DISABLED, policy: ClassificationPolicy | None = None
) -> Level:
    """Pure classification resolver (issue #3251, PM-1; #3545, F03).

    Preconditions (DbC):
    * *inputs* is a :class:`ClassificationInput` and *config* is a :class:`PrivateModeConfig`.
    * Classification never lowers when ``config.enabled`` is ``False`` (#3545).
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
    if policy is not None and not isinstance(policy, ClassificationPolicy):
        raise ClassificationError("resolve: validated classification policy is required when private_mode is enabled")
    if config.enabled and policy is None:
        raise ClassificationError("resolve: validated classification policy is required when private_mode is enabled")

    if policy is not None:
        base_unknown = (
            max_join(policy.default_level, config.default_level, Level.INTERNAL)
            if config.enabled
            else config.default_level
        )
        level = policy.repos[inputs.repo] if inputs.repo and inputs.repo in policy.repos else base_unknown
        for rel_path in inputs.paths:
            for rule in policy.path_globs:
                if _path_matches_glob(rel_path, rule.glob):
                    level = max_join(level, rule.level)

        if inputs.is_legacy_thread and config.enabled:
            level = max_join(level, Level.INTERNAL)

        if inputs.target_id and inputs.target_id in policy.operator_marks:
            mark_level = policy.operator_marks[inputs.target_id]
            if mark_level < level:
                raise ClassificationError("resolve: operator mark cannot downgrade classification level")
            level = max_join(level, mark_level)
    else:
        level = config.default_level

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
        if policy is not None and level < inputs.prior_level:
            raise ClassificationError("resolve: classification downgrade is refused without audited declassify")
        level = max_join(level, inputs.prior_level)

    return level


# --- PM-2 (#3252): immutable versioned envelope + propagation, taint, upgrade requeue, metric ---


@dataclass(frozen=True)
class ClassificationEnvelope:
    """Immutable versioned classification envelope (issue #3252, PM-2)."""

    schema_version: int
    level: Level
    epoch: int = 1
    hops: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "level": self.level.name,
            "epoch": self.epoch,
            "hops": [*self.hops],
        }


@dataclass(frozen=True)
class UpgradeCheckResult:
    """Result of checking a running envelope against a live classification level."""

    envelope: ClassificationEnvelope
    requeue_required: bool
    checkpoint_id: str | None = None


_envelope_ctx = contextvars.ContextVar[ClassificationEnvelope | None]("private_mode_envelope", default=None)
_level_distribution_counts: dict[Level, int] = dict.fromkeys(Level, 0)


def _record_level(level: Level) -> None:
    _level_distribution_counts[level] = _level_distribution_counts.get(level, 0) + 1


def get_level_distribution_counts() -> dict[Level, int]:
    """Return a copy of the in-process level-distribution counter (PM-2 metric)."""
    return dict(_level_distribution_counts)


def reset_level_distribution_counts() -> None:
    """Reset the level-distribution counter (for deterministic tests)."""
    _level_distribution_counts.update(dict.fromkeys(Level, 0))


def stamp_envelope(
    level: Level, *, hop: str = "origin", epoch: int = 1, config: PrivateModeConfig = DISABLED
) -> ClassificationEnvelope:
    """Stamp an initial immutable :class:`ClassificationEnvelope` (never lowers *level* when flag is off)."""
    if not isinstance(level, Level):
        raise ClassificationError("stamp_envelope: level must be a Level")
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("stamp_envelope: config must be a PrivateModeConfig")
    if not isinstance(hop, str) or not hop.strip():
        raise ClassificationError("stamp_envelope: hop must be a non-empty str")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        raise ClassificationError("stamp_envelope: epoch must be an int >= 1")
    effective = level
    _record_level(effective)
    return ClassificationEnvelope(
        schema_version=ENVELOPE_SCHEMA_VERSION, level=effective, epoch=epoch, hops=(hop.strip(),)
    )


def _validate_envelope_fields(ver: object, epoch: object, hops: object, hops_kind: str) -> tuple[int, tuple[str, ...]]:
    if ver != ENVELOPE_SCHEMA_VERSION or isinstance(ver, bool):
        raise ClassificationError(f"validate_envelope: unsupported schema_version (expected {ENVELOPE_SCHEMA_VERSION})")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        raise ClassificationError("validate_envelope: epoch must be an int >= 1")
    if not isinstance(hops, (list, tuple)) or (hops_kind == "tuple" and not isinstance(hops, tuple)):
        raise ClassificationError(f"validate_envelope: hops must be a {hops_kind} of non-empty strings")
    if not all(isinstance(h, str) and h.strip() for h in hops):
        raise ClassificationError(f"validate_envelope: hops must be a {hops_kind} of non-empty strings")
    return epoch, tuple(h.strip() for h in hops)


def validate_envelope(raw: ClassificationEnvelope | Mapping[str, Any]) -> ClassificationEnvelope:
    """Receiver boundary check: validate an envelope or wire dict (DbC)."""
    if isinstance(raw, ClassificationEnvelope):
        _validate_envelope_fields(raw.schema_version, raw.epoch, raw.hops, "tuple")
        if not isinstance(raw.level, Level):
            raise ClassificationError("validate_envelope: level must be a Level")
        return raw
    if not isinstance(raw, Mapping):
        raise ClassificationError("validate_envelope: envelope must be a ClassificationEnvelope or mapping")
    expected_keys = {"schema_version", "level", "epoch", "hops"}
    missing, unknown = sorted(expected_keys - set(raw)), sorted(set(raw) - expected_keys)
    if missing:
        raise ClassificationError(f"validate_envelope: missing required key(s): {', '.join(missing)}")
    if unknown:
        raise ClassificationError(f"validate_envelope: unknown key(s): {', '.join(unknown)}")
    epoch, hops = _validate_envelope_fields(raw["schema_version"], raw["epoch"], raw["hops"], "list")
    level = _parse_level(raw["level"], "validate_envelope", err_cls=ClassificationError)
    return ClassificationEnvelope(schema_version=ENVELOPE_SCHEMA_VERSION, level=level, epoch=epoch, hops=hops)


def propagate_envelope(
    parent: ClassificationEnvelope | Mapping[str, Any], *, hop: str, requested_level: Level | None = None
) -> ClassificationEnvelope:
    """Propagate *parent* across retries, sub-dispatch, forked threads or artifacts (`max_join`)."""
    validated = validate_envelope(parent)
    if not isinstance(hop, str) or not hop.strip():
        raise ClassificationError("propagate_envelope: hop must be a non-empty str")
    if requested_level is not None and not isinstance(requested_level, Level):
        raise ClassificationError("propagate_envelope: requested_level must be a Level")
    next_level = max_join(validated.level, requested_level) if requested_level is not None else validated.level
    next_epoch = validated.epoch + 1 if next_level > validated.level else validated.epoch
    _record_level(next_level)
    return ClassificationEnvelope(
        schema_version=ENVELOPE_SCHEMA_VERSION, level=next_level, epoch=next_epoch, hops=(*validated.hops, hop.strip())
    )


def current_envelope() -> ClassificationEnvelope | None:
    """Return the active :class:`ClassificationEnvelope` from ``contextvars``."""
    return _envelope_ctx.get()


@contextmanager
def envelope_context(envelope: ClassificationEnvelope | Mapping[str, Any]) -> Iterator[ClassificationEnvelope]:
    """Bind a validated :class:`ClassificationEnvelope` to the current execution context (`max_join`)."""
    validated = validate_envelope(envelope)
    outer = _envelope_ctx.get()
    if outer is not None and outer.level > validated.level:
        validated = propagate_envelope(validated, hop="context_join", requested_level=outer.level)
    token = _envelope_ctx.set(validated)
    try:
        yield validated
    finally:
        _envelope_ctx.reset(token)


def copy_context_callable(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> Callable[[], _T]:
    """Wrap *fn* in a snapshot of the current ``contextvars.Context`` for threadpool execution."""
    ctx = contextvars.copy_context()
    return lambda: ctx.run(fn, *args, **kwargs)


def taint_target_level(
    target_level: Level, *, writer_envelope: ClassificationEnvelope | None = None, config: PrivateModeConfig = DISABLED
) -> Level:
    """Writer-context taint: a writer at level *W* elevates its target to ``max(target, W)`` (never lowers)."""
    if not isinstance(target_level, Level):
        raise ClassificationError("taint_target_level: target_level must be a Level")
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("taint_target_level: config must be a PrivateModeConfig")
    active = validate_envelope(writer_envelope) if writer_envelope is not None else _envelope_ctx.get()
    return target_level if active is None else max_join(target_level, active.level)


def taint_target_envelope(
    target: Any, *, writer_envelope: Any = None, config: PrivateModeConfig = DISABLED
) -> ClassificationEnvelope:
    """Apply writer-context taint to *target* envelope, incrementing epoch on upgrade."""
    validated_target = validate_envelope(target)
    tainted_level = taint_target_level(validated_target.level, writer_envelope=writer_envelope, config=config)
    if tainted_level > validated_target.level:
        return propagate_envelope(validated_target, hop="writer_taint", requested_level=tainted_level)
    return validated_target


def check_mid_run_upgrade(
    running: ClassificationEnvelope | Mapping[str, Any], *, live_level: Level, checkpoint_id: str | None = None
) -> UpgradeCheckResult:
    """Detect mid-run classification upgrades and signal checkpoint requeue; refuse downgrades."""
    validated = validate_envelope(running)
    if not isinstance(live_level, Level):
        raise ClassificationError("check_mid_run_upgrade: live_level must be a Level")
    if live_level < validated.level:
        raise ClassificationError("check_mid_run_upgrade: mid-run classification downgrade is refused")
    if live_level > validated.level:
        upgraded = propagate_envelope(validated, hop="mid_run_upgrade", requested_level=live_level)
        return UpgradeCheckResult(envelope=upgraded, requeue_required=True, checkpoint_id=checkpoint_id)
    return UpgradeCheckResult(envelope=validated, requeue_required=False, checkpoint_id=checkpoint_id)


_EMIT_EXPORTS = frozenset(
    "CANARY_FORMS CanaryLeakError CanaryVariants SHARED_SURFACES SURFACE_ALLOWLISTS "
    "SharedProjection assert_no_canary_leak build_canary_variants detect_canary_leaks "
    "emit_shared opaque_identifier".split()
)


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in _EMIT_EXPORTS:
        import private_mode_emit as _pme  # noqa: PLC0415

        return getattr(_pme, name)
    import private_mode_approvals as _pma  # noqa: PLC0415

    if name in _pma.EXPORTS:
        return getattr(_pma, name)
    import private_mode_node_auth as _pmna  # noqa: PLC0415

    if name in _pmna.EXPORTS:
        return getattr(_pmna, name)
    import private_mode_chaos as _pmc  # noqa: PLC0415

    if name in _pmc.EXPORTS:
        return getattr(_pmc, name)
    import private_mode_dry_run as _pmd  # noqa: PLC0415

    if name in _pmd.EXPORTS:
        return getattr(_pmd, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
