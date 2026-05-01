"""Quota enforcement and spend tracking (Wave 3)."""

from __future__ import annotations

import contextlib
import fcntl
import logging
import time
from pathlib import Path

import yaml
from identity import Principal
from local_app_monitoring import collect_local_apps
from runner_lease import lease_manager
from security import safe_yaml_load, validate_config_path

log = logging.getLogger("dashboard.quota_enforcement")


@contextlib.contextmanager
def _locked_yaml_file(path: Path, mode: str = "r+"):
    """Open path and hold an exclusive fcntl lock for the duration of the block.

    Yields the open file object so callers can read/write without releasing
    the lock between operations, preventing concurrent-write corruption (#327).

    Degrades gracefully on platforms that lack fcntl (e.g. Windows dev env).
    """
    path.touch()
    with open(path, mode) as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
        except (AttributeError, OSError):
            pass
        try:
            yield fh
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            except (AttributeError, OSError):
                pass


class QuotaEnforcement:
    def __init__(self, config_dir: Path = Path("config")):
        self.config_dir = config_dir
        self.spend_path = self.config_dir / "principal_spend.yml"
        self.spend_records: dict[str, dict[str, float]] = {}
        self.load_spend()

    def load_spend(self):
        if not self.spend_path.exists():
            return
        try:
            validate_config_path(self.spend_path)
            data = safe_yaml_load(self.spend_path)
            if data and "spend" in data:
                self.spend_records = data["spend"]
        except Exception as exc:
            log.error("Failed to load spend: %s", exc)

    def save_spend(self):
        """Save spend data with security validation (issue #355)."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            validate_config_path(self.spend_path.parent)
            with open(self.spend_path, "w") as f:
                yaml.dump({"spend": self.spend_records}, f)
        except Exception as exc:
            log.error("Failed to save spend: %s", exc)

    def get_today_spend(self, principal_id: str) -> float:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        return self.spend_records.get(principal_id, {}).get(today, 0.0)

    def add_spend(self, principal_id: str, amount_usd: float):
        """Record spend atomically under an exclusive fcntl lock (fixes #327).

        The read-modify-write is done entirely inside the lock so concurrent
        processes accumulate spend correctly instead of last-write-wins.
        """
        today = time.strftime("%Y-%m-%d", time.gmtime())
        self.config_dir.mkdir(parents=True, exist_ok=True)
        validate_config_path(self.spend_path.parent)

        with _locked_yaml_file(self.spend_path, "r+") as fh:
            fh.seek(0)
            raw = fh.read()
            if raw.strip():
                data = yaml.safe_load(raw) or {}
                records: dict[str, dict[str, float]] = data.get("spend", {})
            else:
                records = {}

            if principal_id not in records:
                records[principal_id] = {}
            records[principal_id][today] = records[principal_id].get(today, 0.0) + amount_usd

            fh.seek(0)
            yaml.dump({"spend": records}, fh)
            fh.truncate()

        self.spend_records = records

    def check_dispatch_quota(self, principal: Principal, estimated_cost: float = 0.0) -> tuple[bool, str | None]:
        """Check if a dispatch is allowed based on spend and runner quotas."""
        today_spend = self.get_today_spend(principal.id)
        if today_spend + estimated_cost > principal.quotas.agent_spend_usd_day:
            return (
                False,
                f"Daily spend quota reached ({today_spend:.2f}/{principal.quotas.agent_spend_usd_day:.2f} USD)",
            )

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
