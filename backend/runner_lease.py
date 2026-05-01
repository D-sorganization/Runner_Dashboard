"""Runner lease and claim management (Wave 3).

Enforces per-principal runner quotas and tracks active leases to ensure fair sharing.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import yaml
from identity import Principal
from pydantic import BaseModel, Field
from security import safe_yaml_load, validate_config_path

log = logging.getLogger("dashboard.runner_lease")


class LeaseRecord(BaseModel):
    principal_id: str
    runner_id: str
    acquired_at: float
    expires_at: float | None = None
    task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LeaseManager:
    def __init__(self, config_dir: Path = Path("config")):
        self.config_dir = config_dir
        self.leases_path = self.config_dir / "leases.yml"
        self.leases: list[LeaseRecord] = []
        self.load_leases()

    def load_leases(self):
        if not self.leases_path.exists():
            self.leases = []
            return

        try:
            # Security validation for issue #355: validate path before loading
            validate_config_path(self.leases_path)

            # Use safe_yaml_load which validates path security
            data = safe_yaml_load(self.leases_path)
            if not data or "leases" not in data:
                self.leases = []
                return
            self.leases = [LeaseRecord(**rec) for rec in data["leases"]]
        except Exception as exc:
            log.error("Failed to load leases: %s", exc)
            self.leases = []

    def save_leases(self):
        """Save leases with security validation (issue #355)."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)

            # Security validation: ensure config dir is within allowed roots
            validate_config_path(self.leases_path.parent)

            with open(self.leases_path, "w") as f:
                yaml.dump({"leases": [lease.model_dump() for lease in self.leases]}, f)
        except Exception as exc:
            log.error("Failed to save leases: %s", exc)

    def prune_expired(self):
        now = time.time()
        initial_count = len(self.leases)
        self.leases = [lease for lease in self.leases if lease.expires_at is None or lease.expires_at > now]
        if len(self.leases) < initial_count:
            self.save_leases()

    def get_active_leases(self, principal_id: str | None = None) -> list[LeaseRecord]:
        self.prune_expired()
        if principal_id:
            return [lease for lease in self.leases if lease.principal_id == principal_id]
        return self.leases

    def acquire_lease(
        self,
        principal: Principal,
        runner_id: str,
        duration_seconds: int = 3600,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LeaseRecord:
        """Acquire a lease on a runner, enforcing quotas. Idempotent for same principal."""
        self.prune_expired()

        now = time.time()
        expires_at = now + duration_seconds

        # 1. Check if runner is already leased
        for i, lease in enumerate(self.leases):
            if lease.runner_id == runner_id:
                if lease.principal_id == principal.id:
                    # Update existing lease
                    updated = LeaseRecord(
                        principal_id=principal.id,
                        runner_id=runner_id,
                        acquired_at=lease.acquired_at,
                        expires_at=expires_at,
                        task_id=task_id or lease.task_id,
                        metadata={**(lease.metadata or {}), **(metadata or {})},
                    )
                    self.leases[i] = updated
                    self.save_leases()
                    log.info("Lease UPDATED principal=%s runner=%s task=%s", principal.id, runner_id, task_id)
                    return updated
                raise ValueError(f"Runner {runner_id} is already leased by {lease.principal_id}")

        # 2. Check principal quota
        active_count = len(self.get_active_leases(principal.id))
        if active_count >= principal.quotas.max_runners:
            raise PermissionError(f"Principal {principal.id} has reached runner quota ({principal.quotas.max_runners})")

        # 3. Create lease
        record = LeaseRecord(
            principal_id=principal.id,
            runner_id=runner_id,
            acquired_at=now,
            expires_at=expires_at,
            task_id=task_id,
            metadata=metadata or {},
        )
        self.leases.append(record)
        self.save_leases()
        log.info("Lease ACQUIRED principal=%s runner=%s task=%s", principal.id, runner_id, task_id)
        return record

    def release_lease(self, runner_id: str, principal_id: str | None = None):
        """Release a lease."""
        initial_count = len(self.leases)
        if principal_id:
            self.leases = [
                lease
                for lease in self.leases
                if not (lease.runner_id == runner_id and lease.principal_id == principal_id)
            ]
        else:
            self.leases = [lease for lease in self.leases if lease.runner_id != runner_id]

        if len(self.leases) < initial_count:
            self.save_leases()
            log.info("Lease RELEASED runner=%s", runner_id)


lease_manager = LeaseManager()
