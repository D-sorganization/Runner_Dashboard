"""Fleet event classification for disk-full cordon (RD-OPS-017 / issue #2030).

Pure helpers used by ``fleet_events.classify_fleet_events`` and
``fleet_events.nodes_from_fleet_status`` so cordon logic stays out of the
large fleet feed module.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from disk_full_cordon import multi_host_cordon_alert_needed

if TYPE_CHECKING:
    from fleet_events import FleetEvent, NodeSnapshot


def disk_cordon_active_from_fleet_node(data: Mapping[str, object]) -> bool:
    """True when a fleet status node payload reports an active disk-full cordon."""
    cordon_block = data.get("disk_full_cordon")
    return isinstance(cordon_block, Mapping) and bool(cordon_block.get("active"))


def multi_host_disk_cordon_fleet_events(
    previous: Iterable[NodeSnapshot] | None,
    current: Iterable[NodeSnapshot],
    *,
    ts: int,
    alert_threshold: int,
) -> list[FleetEvent]:
    """Emit a critical fleet event when cordoned host count crosses the RM threshold."""
    from fleet_events import FleetEvent

    current_list = list(current)
    current_count = sum(1 for snap in current_list if snap.disk_cordon_active)
    if previous is None:
        previous_count: int | None = None
    else:
        previous_count = sum(1 for snap in previous if snap.disk_cordon_active)

    if not multi_host_cordon_alert_needed(previous_count, current_count, alert_threshold):
        return []

    return [
        FleetEvent(
            ts=ts,
            severity="critical",
            kind="multi_host_disk_cordon",
            title=f"{current_count} hosts disk-full cordoned",
            detail=(
                f"At least {alert_threshold} fleet hosts are under "
                "disk-full cordon (runner-disk-guard/v1 readmit thresholds)."
            ),
        )
    ]
