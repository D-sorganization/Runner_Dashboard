"""Builder-facing role definition schema (form-only v1 editor)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from staff.bot_builder.capabilities import KNOWN_CAPABILITIES

BUILDER_EDITOR_V1 = "form"

_HOST_VALUES: frozenset[str] = frozenset({"grokbot-first", "rd-first", "both"})
_HOLD_KEYS: frozenset[str] = frozenset(
    {
        "hold",
        "holds",
        "hold_id",
        "hold_ids",
        "lift_hold",
        "safety_limits",
        "safety_limit",
        "grant_hold",
    },
)
_PRIVATE_REF = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}/[a-z0-9][a-z0-9-]{0,62}$")


@dataclass(frozen=True)
class TriggerEdge:
    """Declared trigger edge (loop checks read only these, slice 13)."""

    target_role: str
    event: str


@dataclass(frozen=True)
class ScheduleBounds:
    """Per-role declared schedule bounds validated against locked SchedulePolicy (slice 13)."""

    runs_per_day: int
    concurrency: int = 1
    quiet_hours_utc: tuple[int, int] = (0, 6)


@dataclass(frozen=True)
class BuilderRoleDefinition:
    schema_version: int
    name: str
    persona: str
    instructions: str
    skills: tuple[str, ...]
    model: str | None
    providers: tuple[str, ...]
    schedule: str | None
    triggers: tuple[TriggerEdge, ...]
    host: str
    enabled: bool
    capabilities: tuple[str, ...]
    private_context_ref: str | None
    household_data_opt_in: bool
    description: str = ""
    schedule_bounds: ScheduleBounds | None = None


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"builder role needs non-empty {key!r}")
    return value.strip()


def _reject_hold_keys(data: dict[str, Any]) -> None:
    for key in data:
        if key in _HOLD_KEYS:
            raise ValueError("builder role cannot include hold-related fields")
        lowered = key.lower()
        if lowered in {"hold", "holds"} or lowered.startswith("hold_") or lowered.endswith("_hold"):
            raise ValueError("builder role cannot include hold-related fields")


def _parse_triggers(raw: object) -> tuple[TriggerEdge, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("triggers must be a list")
    edges: list[TriggerEdge] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each trigger must be an object")
        target = item.get("target_role")
        event = item.get("event")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("trigger needs target_role")
        if not isinstance(event, str) or not event.strip():
            raise ValueError("trigger needs event")
        edges.append(TriggerEdge(target_role=target.strip(), event=event.strip()))
    return tuple(edges)


def _parse_schedule_bounds(raw: object) -> ScheduleBounds | None:
    if raw is None:
        return None
    if isinstance(raw, ScheduleBounds):
        return raw
    if not isinstance(raw, dict):
        raise ValueError("schedule_bounds must be an object")
    runs = raw.get("runs_per_day")
    concurrency = raw.get("concurrency", 1)
    quiet = raw.get("quiet_hours_utc", [0, 6])
    if not isinstance(runs, int) or isinstance(runs, bool) or runs < 1:
        raise ValueError("schedule_bounds.runs_per_day must be a positive int")
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise ValueError("schedule_bounds.concurrency must be a positive int")
    if (
        not isinstance(quiet, (list, tuple))
        or len(quiet) != 2
        or not all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 24 for h in quiet)
    ):
        raise ValueError("schedule_bounds.quiet_hours_utc must be [start_hour, end_hour] in 0..24")
    return ScheduleBounds(runs_per_day=runs, concurrency=concurrency, quiet_hours_utc=(quiet[0], quiet[1]))


def parse_builder_role(data: dict[str, Any]) -> BuilderRoleDefinition:
    """DbC: parse and normalise a builder role dict; reject hold fields at the boundary."""
    if not isinstance(data, dict):
        raise ValueError("builder role must be an object")
    _reject_hold_keys(data)
    version = data.get("schema_version")
    if version != 1:
        raise ValueError("unsupported schema_version")

    skills_raw = data.get("skills") or []
    if not isinstance(skills_raw, list) or not all(isinstance(s, str) for s in skills_raw):
        raise ValueError("skills must be a list of strings")

    providers_raw = data.get("providers") or []
    if not isinstance(providers_raw, list) or not providers_raw:
        raise ValueError("providers must be a non-empty list")
    if not all(isinstance(p, str) and p.strip() for p in providers_raw):
        raise ValueError("providers must be non-empty strings")

    caps_raw = data.get("capabilities") or []
    if not isinstance(caps_raw, list) or not all(isinstance(c, str) for c in caps_raw):
        raise ValueError("capabilities must be a list of strings")

    host = data.get("host", "grokbot-first")
    if not isinstance(host, str) or host not in _HOST_VALUES:
        raise ValueError("invalid host setting")

    model = data.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("model must be a string when set")

    schedule = data.get("schedule")
    if schedule is not None and not isinstance(schedule, str):
        raise ValueError("schedule must be a string or null")

    ref = data.get("private_context_ref")
    if ref is not None:
        if not isinstance(ref, str) or not _PRIVATE_REF.match(ref.strip()):
            raise ValueError("private_context_ref must match role-id/store-ref pattern")
        ref = ref.strip()
    else:
        ref = None

    opt_in = bool(data.get("household_data_opt_in", False))

    description_raw = data.get("description", "")
    if description_raw is None:
        description = ""
    elif isinstance(description_raw, str):
        description = description_raw.strip()
    else:
        raise ValueError("description must be a string when set")

    schedule_bounds = _parse_schedule_bounds(data.get("schedule_bounds"))

    for cap in caps_raw:
        if cap.startswith("hold."):
            raise ValueError("hold capabilities are not grantable")

    unknown = [c for c in caps_raw if c not in KNOWN_CAPABILITIES]
    if unknown:
        raise ValueError("unknown capability id")

    return BuilderRoleDefinition(
        schema_version=1,
        name=_require_str(data, "name"),
        persona=_require_str(data, "persona"),
        instructions=_require_str(data, "instructions"),
        skills=tuple(s.strip() for s in skills_raw if s.strip()),
        model=model.strip() if isinstance(model, str) else None,
        providers=tuple(p.strip() for p in providers_raw),
        schedule=schedule.strip() if isinstance(schedule, str) and schedule.strip() else None,
        triggers=_parse_triggers(data.get("triggers")),
        host=host,
        enabled=bool(data.get("enabled", False)),
        capabilities=tuple(caps_raw),
        private_context_ref=ref,
        household_data_opt_in=opt_in,
        description=description,
        schedule_bounds=schedule_bounds,
    )
