"""Flatten fleet status payloads into ``NodeSnapshot`` records (issue #863)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from disk_full_cordon_fleet_events import disk_cordon_active_from_fleet_node

if TYPE_CHECKING:
    from fleet_events import NodeSnapshot


def nodes_from_fleet_status(
    status: Mapping[str, Mapping[str, object]],
) -> list[NodeSnapshot]:
    """Flatten the nested ``/api/fleet/status`` payload into ``NodeSnapshot``s.

    Law of Demeter boundary: the nested-dict reaching happens here, once, so
    the classifier and store only ever see flat typed records.
    """
    from fleet_events import NodeSnapshot as _NodeSnapshot

    out: list[_NodeSnapshot] = []
    for name, data in status.items():
        if not isinstance(data, Mapping):
            continue
        status_str = data.get("status")
        offline_reason = data.get("offline_reason")
        online = status_str != "offline" and offline_reason != "disk-pressure"
        disk = data.get("disk")
        free_gb: float | None = None
        percent: float | None = None
        if isinstance(disk, Mapping):
            host = disk.get("windows_host")
            metric = host if isinstance(host, Mapping) else disk
            raw_free = metric.get("free_gb")
            raw_percent = metric.get("percent")
            if isinstance(raw_free, (int, float)):
                free_gb = float(raw_free)
            if isinstance(raw_percent, (int, float)):
                percent = float(raw_percent)
        out.append(
            _NodeSnapshot(
                name=name,
                online=bool(online),
                disk_free_gb=free_gb,
                disk_percent=percent,
                offline_reason=(str(offline_reason) if offline_reason is not None else None),
                offline_detail=(str(data.get("offline_detail")) if data.get("offline_detail") is not None else None),
                disk_cordon_active=disk_cordon_active_from_fleet_node(data),
            )
        )
    return out
