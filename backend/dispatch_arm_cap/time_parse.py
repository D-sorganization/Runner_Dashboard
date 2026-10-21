"""ISO timestamp parsing shared by store and admission (DRY)."""

from __future__ import annotations

from datetime import datetime


def parse_iso_timestamp(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))
