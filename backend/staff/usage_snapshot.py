"""Fleet-wide provider usage snapshot ingest and view (#3042, parent RM #2886).

Usage-Tracker POSTs each hourly ``provider-availability.v1`` snapshot to this
node. This module validates the payload (fail closed on any contract
violation), keeps only the newest snapshot (never regresses to stale data,
same pattern as :mod:`staff.quota`), and renders the per-provider,
per-machine view the Plan quota panel polls. It never calls a provider CLI
and never measures quota itself; it only relays what Usage-Tracker reported.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from staff.quota import STALE_SNAPSHOT
from staff.store import _config_dir

SCHEMA_VERSION = "provider-availability.v1"
STATE_FILE = "staff_usage_snapshot.json"
MAX_FUTURE_SKEW = timedelta(minutes=10)


class SnapshotRejected(Exception):
    """An ingest payload failed a contract check; ``field`` names the first failure."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        super().__init__(f"{field}: {reason}")


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotRejected(field, "must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotRejected(field, "not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SnapshotRejected(field, "must be timezone-aware")
    return parsed.astimezone(UTC)


def _validate_percent(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SnapshotRejected(field, "must be a number or null")
    if not 0 <= float(value) <= 100:
        raise SnapshotRejected(field, "must be between 0 and 100")
    return float(value)


def _validate_reading(raw: Any, provider_index: int, reading_index: int) -> dict[str, Any]:
    prefix = f"providers[{provider_index}].rate_limits.readings[{reading_index}]"
    if not isinstance(raw, Mapping):
        raise SnapshotRejected(prefix, "must be an object")
    machine = raw.get("machine")
    if not isinstance(machine, str) or not machine.strip():
        raise SnapshotRejected(f"{prefix}.machine", "must be a non-empty string")
    observed_at = _parse_timestamp(raw.get("observed_at"), f"{prefix}.observed_at")
    used_percent = _validate_percent(raw.get("used_percent"), f"{prefix}.used_percent")
    resets_at = raw.get("resets_at")
    if resets_at is not None:
        resets_at = _iso(_parse_timestamp(resets_at, f"{prefix}.resets_at"))
    return {
        "machine": machine,
        "observed_at": _iso(observed_at),
        "used_percent": used_percent,
        "resets_at": resets_at,
        "source": str(raw.get("source") or ""),
    }


def _validate_provider(raw: Any, index: int) -> dict[str, Any]:
    prefix = f"providers[{index}]"
    if not isinstance(raw, Mapping):
        raise SnapshotRejected(prefix, "must be an object")
    provider = raw.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise SnapshotRejected(f"{prefix}.provider", "must be a non-empty string")
    quota_used_pct = _validate_percent(raw.get("quota_used_pct"), f"{prefix}.quota_used_pct")
    rate_limits = raw.get("rate_limits")
    readings_raw = rate_limits.get("readings", []) if isinstance(rate_limits, Mapping) else []
    if not isinstance(readings_raw, list):
        raise SnapshotRejected(f"{prefix}.rate_limits.readings", "must be a list")
    readings = [_validate_reading(r, index, i) for i, r in enumerate(readings_raw)]
    reset_at = raw.get("reset_at")
    if reset_at is not None:
        reset_at = _iso(_parse_timestamp(reset_at, f"{prefix}.reset_at"))
    return {
        "provider": provider,
        "label": raw.get("label") if isinstance(raw.get("label"), str) else provider,
        "status": raw.get("status") if isinstance(raw.get("status"), str) else "unknown",
        "quota_used_pct": quota_used_pct,
        "quota_limit": raw.get("quota_limit"),
        "reset_at": reset_at,
        "near_limit_flag": bool(raw.get("near_limit_flag")),
        "readings": readings,
    }


@dataclass(frozen=True)
class UsageSnapshot:
    """One validated, fully-normalized ``provider-availability.v1`` snapshot."""

    schema_version: str
    checked_at: datetime
    received_at: datetime
    providers: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "checked_at": _iso(self.checked_at),
            "received_at": _iso(self.received_at),
            "providers": [dict(p) for p in self.providers],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UsageSnapshot:
        return cls(
            schema_version=str(data["schema_version"]),
            checked_at=_parse_timestamp(data["checked_at"], "checked_at"),
            received_at=_parse_timestamp(data["received_at"], "received_at"),
            providers=tuple(data.get("providers") or ()),
        )


def validate_payload(raw: Mapping[str, Any], *, now: datetime) -> UsageSnapshot:
    """Validate an ingested ``provider-availability.v1`` payload (#3042).

    Pre: ``raw`` is the parsed JSON body; ``now`` is tz-aware.
    Post: a snapshot whose timestamps are normalized to UTC.
    Raises: :class:`SnapshotRejected` naming the first field that failed.
    """
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotRejected("schema_version", f"must be {SCHEMA_VERSION!r}")
    checked_at = _parse_timestamp(raw.get("checked_at"), "checked_at")
    if checked_at > now + MAX_FUTURE_SKEW:
        raise SnapshotRejected("checked_at", "must not be more than 10 minutes in the future")
    providers_raw = raw.get("providers")
    if not isinstance(providers_raw, list):
        raise SnapshotRejected("providers", "must be a list")
    providers = tuple(_validate_provider(p, i) for i, p in enumerate(providers_raw))
    return UsageSnapshot(schema_version=SCHEMA_VERSION, checked_at=checked_at, received_at=now, providers=providers)


def state_path() -> Path:
    return Path(os.environ.get("STAFF_USAGE_SNAPSHOT_STATE", str(_config_dir() / STATE_FILE))).expanduser()


class UsageSnapshotStore:
    """Newest ingested fleet usage snapshot, persisted as JSON (atomic replace)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path or state_path()

    def _load(self) -> UsageSnapshot | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return UsageSnapshot.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    def get(self) -> UsageSnapshot | None:
        with self._lock:
            return self._load()

    def record(self, snapshot: UsageSnapshot) -> bool:
        """Keep ``snapshot`` unless a newer one (by ``checked_at``) is already stored."""
        with self._lock:
            existing = self._load()
            if existing is not None and existing.checked_at >= snapshot.checked_at:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
            return True


_STORE = UsageSnapshotStore()


def default_store() -> UsageSnapshotStore:
    return _STORE


def view(now: datetime, *, store: UsageSnapshotStore | None = None) -> dict[str, Any]:
    """Body of ``GET /api/staff/usage/snapshot``: per-provider, per-machine quota + staleness.

    Post: every machine row has ``age_seconds >= 0``; no snapshot yet never 500s.
    """
    snapshot = (store or default_store()).get()
    if snapshot is None:
        return {
            "checked_at": None,
            "received_at": None,
            "snapshot_age_seconds": None,
            "snapshot_stale": True,
            "providers": [],
        }
    snapshot_age = max(0.0, (now - snapshot.received_at).total_seconds())
    providers = []
    for provider in snapshot.providers:
        machines = []
        for reading in provider["readings"]:
            observed_at = _parse_timestamp(reading["observed_at"], "observed_at")
            age_seconds = max(0.0, (now - observed_at).total_seconds())
            machines.append(
                {
                    "machine": reading["machine"],
                    "used_percent": reading["used_percent"],
                    "resets_at": reading["resets_at"],
                    "observed_at": reading["observed_at"],
                    "age_seconds": age_seconds,
                    "stale": age_seconds > STALE_SNAPSHOT.total_seconds(),
                }
            )
        providers.append(
            {
                "provider": provider["provider"],
                "label": provider["label"],
                "status": provider["status"],
                "quota_used_pct": provider["quota_used_pct"],
                "quota_limit": provider["quota_limit"],
                "reset_at": provider["reset_at"],
                "near_limit_flag": provider["near_limit_flag"],
                "machines": machines,
            }
        )
    return {
        "checked_at": _iso(snapshot.checked_at),
        "received_at": _iso(snapshot.received_at),
        "snapshot_age_seconds": snapshot_age,
        "snapshot_stale": snapshot_age > STALE_SNAPSHOT.total_seconds(),
        "providers": providers,
    }
