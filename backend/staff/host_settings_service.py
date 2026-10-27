"""Apply host-settings mutations (issue #3101): shared by API and overlay file edits."""

from __future__ import annotations

import logging
from typing import Any

from staff.audit import record_audit
from staff.holds import HoldsList
from staff.host_settings_contract import HostSettingsDocument, load_host_settings, save_host_settings

_log = logging.getLogger("dashboard.staff.host_settings")


class HostSettingsError(ValueError):
    """Operator-facing validation failure."""


def _holds_snapshot(holds: HoldsList) -> list[dict[str, Any]]:
    return [h.to_dict() for h in holds.load()]


def apply_host_settings_update(
    doc: HostSettingsDocument,
    *,
    confirmed: bool,
    principal: str,
    holds: HoldsList | None = None,
    path_store: bool = True,
) -> HostSettingsDocument:
    """Persist settings after confirmation; audit flips; holds must stay unchanged."""
    if not confirmed:
        raise HostSettingsError("confirmation required to change host settings")
    holds_list = holds or HoldsList()
    before_holds = _holds_snapshot(holds_list)
    before_doc = load_host_settings()
    before_digest = (before_doc or HostSettingsDocument.empty()).to_dict()
    after_digest = doc.to_dict()
    if path_store:
        save_host_settings(doc)
    after_holds = _holds_snapshot(holds_list)
    if before_holds != after_holds:
        raise HostSettingsError("holds changed during host settings update (refused)")
    if before_digest != after_digest:
        record_audit(
            action="host_flip",
            target="host_settings",
            principal=principal,
            surface="api",
            outcome="success",
            detail={"before": before_digest, "after": after_digest},
            fail_closed=True,
        )
        _log.info("role_sync: host flip handoff snapshot queued")
        _log.info("role_sync: leader lease transfer requested")
    return doc
