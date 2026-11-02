"""Hub-visible audit for Windows/WSL provider credential mirror drift (#3820)."""

from __future__ import annotations

import importlib.util
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.staff.credential_mirror_health")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SYNC_SCRIPT = _REPO_ROOT / "deploy" / "sync_provider_credentials.py"


def _load_sync_module() -> Any:
    spec = importlib.util.spec_from_file_location("sync_provider_credentials", _SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"sync script missing: {_SYNC_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, mod)
    spec.loader.exec_module(mod)
    return mod


def probe_credential_mirror_health() -> dict[str, Any]:
    """Run mirror audit on this node; never raises and never returns secret contents."""
    checked_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if not _SYNC_SCRIPT.is_file():
        return {"checked_at": checked_at, "skipped": True, "reason": "sync_script_missing", "ok": True}
    try:
        sync_mod = _load_sync_module()
        audit = sync_mod.audit_credential_mirrors()
    except Exception as exc:  # noqa: BLE001
        log.warning("credential mirror audit failed: %s", exc)
        return {"checked_at": checked_at, "skipped": True, "reason": "audit_error", "ok": True}
    return {"checked_at": checked_at, **audit}


def attention_items_from_mirror_health(report: dict[str, Any], *, machine: str) -> list[dict[str, Any]]:
    if report.get("skipped") or report.get("ok") is True:
        return []
    items: list[dict[str, Any]] = []
    for provider, row in (report.get("providers") or {}).items():
        stale = row.get("stale_copies") or []
        if not stale:
            continue
        pid = "antigravity" if provider == "antigravity" else str(provider)
        items.append(
            {
                "id": f"credential-mirror-{machine}-{pid}",
                "role": "hub",
                "provider": pid,
                "machine": machine,
                "status": "attention",
                "failure_class": "credential_mirror_stale",
                "error": f"{pid} credential mirror stale ({len(stale)} copy/copies behind source)",
                "remediation": (
                    "Run node self-heal credential sync on "
                    f"{machine} (deploy/node-self-heal.sh or runner-dashboard-self-heal.timer); "
                    "see docs/runbooks/durable-provider-credentials.md."
                ),
                "stale_copy_count": len(stale),
            }
        )
    return items
