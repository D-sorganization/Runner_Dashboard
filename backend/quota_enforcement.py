"""Quota enforcement and spend tracking (Wave 3)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import yaml
from identity import Principal
from local_app_monitoring import collect_local_apps
from runner_lease import lease_manager
from security import safe_yaml_load, validate_config_path

log = logging.getLogger("dashboard.quota_enforcement")


class QuotaEnforcement:
    def __init__(self, config_dir: Path = Path("config")):
        self.config_dir = config_dir
        self.spend_path = self.config_dir / "principal_spend.yml"
        self.spend_records: dict[str, dict[str, float]] = {}  # principal_id -> {date -> spend}
        self.load_spend()

    def load_spend(self):
        if not self.spend_path.exists():
            return
        try:
            # Security validation for issue #355: validate path before loading
            validate_config_path(self.spend_path)

            # Use safe_yaml_load which validates path security
            data = safe_yaml_load(self.spend_path)
            if data and "spend" in data:
                self.spend_records = data["spend"]
        except Exception as exc:
            log.error("Failed to load spend: %s", exc)

    def save_spend(self):
        """Save spend data with security validation (issue #355)."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)

            # Security validation: ensure config dir is within allowed roots
            validate_config_path(self.spend_path.parent)

            with open(self.spend_path, "w") as f:
                yaml.dump({"spend": self.spend_records}, f)
        except Exception as exc:
            log.error("Failed to save spend: %s", exc)

    def get_today_spend(self, principal_id: str) -> float:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        return self.spend_records.get(principal_id, {}).get(today, 0.0)

    def add_spend(self, principal_id: str, amount_usd: float):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if principal_id not in self.spend_records:
            self.spend_records[principal_id] = {}
        self.spend_records[principal_id][today] = self.spend_records[principal_id].get(today, 0.0) + amount_usd
        self.save_spend()

    def check_dispatch_quota(self, principal: Principal, estimated_cost: float = 0.0) -> tuple[bool, str | None]:
        """Check if a dispatch is allowed based on spend and runner quotas."""
        # 1. Check spend quota
        today_spend = self.get_today_spend(principal.id)
        if today_spend + estimated_cost > principal.quotas.agent_spend_usd_day:
            return (
                False,
                f"Daily spend quota reached ({today_spend:.2f}/{principal.quotas.agent_spend_usd_day:.2f} USD)",
            )

        # 2. Check runner quota
        active_leases = lease_manager.get_active_leases(principal.id)
        if len(active_leases) >= principal.quotas.max_runners:
            return False, f"Runner quota reached ({len(active_leases)}/{principal.quotas.max_runners})"

        return True, None

    def get_local_app_usage(self, principal_id: str) -> int:
        """Count local apps owned by the principal."""
        try:
            reports = collect_local_apps()
            owned = [app for app in reports.get("apps", []) if app.get("owner") == principal_id]
            return len(owned)
        except Exception as exc:
            log.error("Failed to check local app usage: %s", exc)
            return 0

    def check_local_app_quota(self, principal: Principal) -> tuple[bool, str | None]:
        usage = self.get_local_app_usage(principal.id)
        if usage >= principal.quotas.local_app_slots:
            return False, f"Local app slots reached ({usage}/{principal.quotas.local_app_slots})"
        return True, None


quota_enforcement = QuotaEnforcement()
