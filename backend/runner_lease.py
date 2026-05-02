"""Runner lease and claim management (Wave 3).

Enforces per-principal runner quotas and tracks active leases to ensure fair sharing.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import time
from pathlib import Path
from typing import Any

import yaml
from identity import Principal
from pydantic import BaseModel, Field
from security import safe_yaml_load, validate_config_path

log = logging.getLogger("dashboard.runner_lease")


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
            validate_config_path(self.leases_path)
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
            validate_config_path(self.leases_path.parent)
            with open(self.leases_path, "w") as f:
                yaml.dump({"leases": [lease.model_dump() for lease in self.leases]}, f)
        except Exception as exc:
            log.error("Failed to save leases: %s", exc)

    def _atomic_read_modify_write(self, mutate):
        """Perform a process-safe read-modify-write on leases.yml.

        mutate receives the current expiry-pruned list of LeaseRecord objects
        and must return the new list. The file is re-read inside the exclusive
        lock so writes from concurrent processes are incorporated before the
        mutation is applied (fixes issue #327).
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        validate_config_path(self.leases_path.parent)

        with _locked_yaml_file(self.leases_path, "r+") as fh:
            fh.seek(0)
            raw = fh.read()
            if raw.strip():
                data = yaml.safe_load(raw) or {}
                records = [LeaseRecord(**rec) for rec in data.get("leases", [])]
            else:
                records = []

            now = time.time()
            records = [r for r in records if r.expires_at is None or r.expires_at > now]

            new_records = mutate(records)

            fh.seek(0)
            yaml.dump({"leases": [r.model_dump() for r in new_records]}, fh)
            fh.truncate()

        self.leases = new_records

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
        """Acquire a lease on a runner, enforcing quotas. Idempotent for same principal.

        The entire read-check-write cycle is protected by an exclusive fcntl lock
        so concurrent processes cannot produce duplicate or corrupt lease state
        (fixes issue #327).
        """
        result: list[LeaseRecord] = []

        def _mutate(records: list[LeaseRecord]) -> list[LeaseRecord]:
            now = time.time()
            expires_at = now + duration_seconds

            for i, lease in enumerate(records):
                if lease.runner_id == runner_id:
                    if lease.principal_id == principal.id:
                        updated = LeaseRecord(
                            principal_id=principal.id,
                            runner_id=runner_id,
                            acquired_at=lease.acquired_at,
                            expires_at=expires_at,
                            task_id=task_id or lease.task_id,
                            metadata={**(lease.metadata or {}), **(metadata or {})},
                        )
                        records[i] = updated
                        result.append(updated)
                        log.info("Lease UPDATED principal=%s runner=%s task=%s", principal.id, runner_id, task_id)
                        return records
                    raise ValueError(f"Runner {runner_id} is already leased by {lease.principal_id}")

            active_count = sum(1 for r in records if r.principal_id == principal.id)
            if active_count >= principal.quotas.max_runners:
                raise PermissionError(
                    f"Principal {principal.id} has reached runner quota ({principal.quotas.max_runners})"
                )

            record = LeaseRecord(
                principal_id=principal.id,
                runner_id=runner_id,
                acquired_at=now,
                expires_at=expires_at,
                task_id=task_id,
                metadata=metadata or {},
            )
            records.append(record)
            result.append(record)
            log.info("Lease ACQUIRED principal=%s runner=%s task=%s", principal.id, runner_id, task_id)
            return records

        self._atomic_read_modify_write(_mutate)
        return result[0]

    def release_lease(self, runner_id: str, principal_id: str | None = None):
        """Release a lease.

        Protected by an exclusive fcntl lock so concurrent releases do not
        re-introduce stale entries written by a racing process (fixes #327).
        """
        released: list[bool] = []

        def _mutate(records: list[LeaseRecord]) -> list[LeaseRecord]:
            if principal_id:
                new = [r for r in records if not (r.runner_id == runner_id and r.principal_id == principal_id)]
            else:
                new = [r for r in records if r.runner_id != runner_id]
            released.append(len(new) < len(records))
            return new

        self._atomic_read_modify_write(_mutate)
        if released and released[0]:
            log.info("Lease RELEASED runner=%s", runner_id)


lease_manager = LeaseManager()
