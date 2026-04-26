"""Usage monitoring model primitives for the runner dashboard.

This module is intentionally side-effect free. It parses a local usage-source
configuration file or in-memory mapping and normalizes it into a dashboard-safe
summary payload with the fields needed by the first monitoring model:

- current period
- remaining budget
- projected burn
- last refresh timestamp
- confidence score

The design avoids direct secret access and avoids live API calls so tests can
exercise the normalization logic with fixture data only.
"""

from __future__ import annotations

import datetime as _dt_mod
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Any

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

SCHEMA_VERSION = "usage-monitoring.v1"
DEFAULT_USAGE_SOURCES_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "usage_sources.json"
)


def _ensure_mapping(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, Mapping):
        return dict(data)
    raise TypeError("usage source config must be a mapping")


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("boolean values are not valid numeric usage fields")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        return float(value)
    return None


def _coerce_confidence(value: Any) -> float:
    confidence = _coerce_float(value)
    if confidence is None:
        return 0.5
    return max(0.0, min(1.0, confidence))


def _normalize_timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        return (
            datetime.fromisoformat(normalized)
            .astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    raise TypeError("timestamps must be ISO strings or datetime objects")


def _normalize_period(data: Any) -> dict[str, str | None]:
    if data is None:
        return {"label": None, "start": None, "end": None}
    if isinstance(data, str):
        return {"label": data.strip() or None, "start": None, "end": None}
    period = _ensure_mapping(data)
    return {
        "label": (str(period["label"]).strip() if period.get("label") else None),
        "start": _normalize_timestamp(period.get("start")),
        "end": _normalize_timestamp(period.get("end")),
    }


def _period_label(period: dict[str, str | None]) -> str | None:
    if period.get("label"):
        return period["label"]
    if period.get("start") and period.get("end"):
        return f"{period['start']}..{period['end']}"
    return None


@dataclass(frozen=True, slots=True)
class UsageSourceConfig:
    """Declarative configuration for one usage source."""

    name: str
    kind: str
    label: str = ""
    usage_unit: str = ""
    usage_limit: float | None = None
    current_usage: float | None = None
    projected_burn: float | None = None
    current_period: dict[str, str | None] = field(default_factory=dict)
    last_refresh: str | None = None
    confidence: float = 0.5
    secret_handling: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["current_period"] = dict(self.current_period)
        data["secret_handling"] = dict(self.secret_handling)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UsageSourceConfig:
        source = _ensure_mapping(data)
        name = str(source.get("name", "")).strip()
        if not name:
            raise ValueError("usage source name is required")
        kind = str(source.get("kind", "")).strip()
        if not kind:
            raise ValueError(f"usage source kind is required for {name}")
        return cls(
            name=name,
            kind=kind,
            label=str(source.get("label", "")).strip(),
            usage_unit=str(source.get("usage_unit", "")).strip(),
            usage_limit=_coerce_float(source.get("usage_limit")),
            current_usage=_coerce_float(source.get("current_usage")),
            projected_burn=_coerce_float(source.get("projected_burn")),
            current_period=_normalize_period(source.get("current_period")),
            last_refresh=_normalize_timestamp(source.get("last_refresh")),
            confidence=_coerce_confidence(source.get("confidence")),
            secret_handling=_ensure_mapping(source.get("secret_handling")),
            notes=str(source.get("notes", "")).strip(),
        )


def load_usage_sources_config(
    path: Path | str | None = None,
) -> list[UsageSourceConfig]:
    """Load usage-source definitions from JSON config or return an empty list."""

    config_path = (
        path or os.environ.get("USAGE_SOURCES_PATH") or DEFAULT_USAGE_SOURCES_PATH
    )
    resolved = Path(config_path)
    if not resolved.exists():
        return []
    data = json.loads(resolved.read_text())
    return parse_usage_sources_config(data)


def parse_usage_sources_config(data: Any) -> list[UsageSourceConfig]:
    """Parse a raw config payload into source config dataclasses."""

    if isinstance(data, Mapping):
        sources = data.get("usage_sources", [])
    else:
        sources = data
    if not isinstance(sources, list):
        raise TypeError("usage_sources must be a list")
    return [
        (
            item
            if isinstance(item, UsageSourceConfig)
            else UsageSourceConfig.from_dict(item)
        )
        for item in sources
    ]


def normalize_usage_source(
    source: UsageSourceConfig,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Normalize one source into the dashboard summary shape."""

    period = dict(source.current_period)
    period_label = _period_label(period)
    remaining = (
        round(source.usage_limit - source.current_usage, 2)
        if source.usage_limit is not None and source.current_usage is not None
        else None
    )
    projected_burn = source.projected_burn
    if projected_burn is None and source.current_usage is not None:
        projected_burn = source.current_usage
    summary = {
        "name": source.name,
        "kind": source.kind,
        "label": source.label or source.name,
        "usage_unit": source.usage_unit,
        "current_period": period,
        "current_period_label": period_label,
        "current_usage": source.current_usage,
        "usage_limit": source.usage_limit,
        "remaining": remaining,
        "projected_burn": projected_burn,
        "last_refresh": source.last_refresh,
        "confidence": source.confidence,
        "secret_handling": dict(source.secret_handling),
        "notes": source.notes,
    }
    if observed_at is not None:
        summary["observed_at"] = (
            observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        )
    return summary


def normalize_usage_summary(
    config: Any,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Normalize a usage config payload into an aggregate dashboard summary."""

    sources = parse_usage_sources_config(config)
    normalized_sources = [
        normalize_usage_source(source, observed_at=observed_at) for source in sources
    ]
    current_period_labels = [
        item["current_period_label"]
        for item in normalized_sources
        if item.get("current_period_label")
    ]
    if current_period_labels and len(set(current_period_labels)) == 1:
        summary_period = current_period_labels[0]
    elif current_period_labels:
        summary_period = "mixed"
    else:
        summary_period = None

    last_refresh_values = [
        item["last_refresh"] for item in normalized_sources if item.get("last_refresh")
    ]
    confidence_values = [
        item["confidence"]
        for item in normalized_sources
        if item.get("confidence") is not None
    ]

    current_usage_values = [
        item["current_usage"]
        for item in normalized_sources
        if item["current_usage"] is not None
    ]
    limit_values = [
        item["usage_limit"]
        for item in normalized_sources
        if item["usage_limit"] is not None
    ]
    remaining_values = [
        item["remaining"]
        for item in normalized_sources
        if item["remaining"] is not None
    ]
    projected_burn_values = [
        item["projected_burn"]
        for item in normalized_sources
        if item["projected_burn"] is not None
    ]

    def _sum_or_none(values: list[float]) -> float | None:
        return round(sum(values), 2) if values else None

    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "current_period": summary_period,
            "current_usage": _sum_or_none(current_usage_values),
            "usage_limit": _sum_or_none(limit_values),
            "remaining": _sum_or_none(remaining_values),
            "projected_burn": _sum_or_none(projected_burn_values),
            "last_refresh": max(last_refresh_values) if last_refresh_values else None,
            "confidence": (
                round(fmean(confidence_values), 2) if confidence_values else 0.0
            ),
        },
        "usage_sources": normalized_sources,
    }
