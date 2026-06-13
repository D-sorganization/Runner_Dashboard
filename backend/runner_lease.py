"""Runner lease and claim management (Wave 3).

Enforces per-principal runner quotas and tracks active leases to ensure fair sharing.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

# fcntl is Unix-only; degrade gracefully on Windows so module import
# succeeds. The usage sites already catch AttributeError/OSError, so this
# only matters for module-load on Windows clones — see the matching
# tolerant import in backend/quota_enforcement.py for the longer rationale.
try:
    import fcntl  # type: ignore[import-not-found,unused-ignore]
except ImportError:  # pragma: no cover - Windows-only path
    fcntl = None  # type: ignore[assignment]

import yaml
from identity import Principal
from pydantic import BaseModel, Field
from security import safe_yaml_load, validate_config_path

log = logging.getLogger("dashboard.runner_lease")

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows development/runtime path.
    fcntl = None


@contextlib.contextmanager
def _locked_yaml_file(path: Path, mode: str = "r+"):
    """Open path and hold an exclusive fcntl lock for the duration of the block.

    Yields the open file object so callers can read/write without releasing
    the lock between operations, preventing concurrent-write corruption (#327).

    Degrades gracefully on platforms that lack fcntl (e.g. Windows dev env).
    """
    path.touch()
    with open(path, mode) as fh:
        flock = getattr(fcntl, "flock", None) if fcntl is not None else None
        lock_ex = getattr(fcntl, "LOCK_EX", None) if fcntl is not None else None
        lock_un = getattr(fcntl, "LOCK_UN", None) if fcntl is not None else None
        if flock is not None and lock_ex is not None:
            try:
                flock(fh, lock_ex)
            except OSError:
                pass
        try:
            yield fh
        finally:
            if flock is not None and lock_un is not None:
                try:
                    flock(fh, lock_un)
                except OSError:
                    pass


class LeaseRecord(BaseModel):
    principal_id: str
    runner_id: str
    acquired_at: float
    expires_at: float | None = None
    task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _default_config_dir() -> Path:
    configured = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path("~/.config/runner-dashboard").expanduser()


class LeaseManager:
    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or _default_config_dir()
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
        """Atomically persist the in-memory leases to ``leases.yml``.

        Security validation (issue #355). Crash-safety (issue #936): writes via
        a temp file + ``os.replace`` so a crash mid-dump can never leave partial
        YAML — ``load_leases`` would otherwise silently reset a truncated file to
        ``[]``, wiping every lease. Readers see either the old or the new
        complete file, never a half-written one. Mirrors the pattern in
        ``identity.IdentityStore.save_principals``.

        Note: callers needing process-safe concurrency (the reaper, pruning)
        must go through :meth:`_atomic_read_modify_write`, which holds the
        exclusive lock across read+modify+write. ``save_leases`` only guarantees
        a single write is atomic; it does not re-read under lock.
        """
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            validate_config_path(self.leases_path.parent)
            payload = {"leases": [lease.model_dump() for lease in self.leases]}
            fd, tmp_path = tempfile.mkstemp(
                dir=self.leases_path.parent,
                prefix=".tmp-leases-",
                suffix=".yml",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    yaml.dump(payload, fh)
                os.replace(tmp_path, self.leases_path)
            except (OSError, yaml.YAMLError):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except Exception as exc:
            log.error("Failed to save leases: %s", exc)

    def _atomic_read_modify_write(self, mutate, on_pruned=None):
        """Perform a process-safe read-modify-write on leases.yml.

        mutate receives the current expiry-pruned list of LeaseRecord objects
        and must return the new list. The file is re-read inside the exclusive
        lock so writes from concurrent processes are incorporated before the
        mutation is applied (fixes issue #327).

        ``on_pruned``, when supplied, is called with the number of expired
        records dropped during the in-lock expiry sweep — used by
        :meth:`prune_expired` to report an accurate removed count computed from
        the freshly re-read file rather than a stale in-memory snapshot (#936).
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
            pre_prune_count = len(records)
            records = [r for r in records if r.expires_at is None or r.expires_at > now]
            if on_pruned is not None:
                on_pruned(pre_prune_count - len(records))

            new_records = mutate(records)

            # Crash-safe persist (issue #936): write a temp file and atomically
            # ``os.replace`` it over leases.yml. A crash mid-write leaves either
            # the old or the new complete file, never partial YAML. The exclusive
            # fcntl lock on the (locked) handle still serializes concurrent
            # processes: every writer locks the same path before reading, so the
            # replace cannot interleave with another process's read-modify cycle.
            payload = {"leases": [r.model_dump() for r in new_records]}
            fd, tmp_path = tempfile.mkstemp(
                dir=self.leases_path.parent,
                prefix=".tmp-leases-",
                suffix=".yml",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
                    yaml.dump(payload, tmp_fh)
                # On platforms without fcntl (Windows dev/test) there is no real
                # advisory lock and an open handle blocks os.replace over the
                # target. Close the lock handle before replacing there. On POSIX
                # we keep the handle open across the replace so the advisory lock
                # is held for the entire read-modify-write window.
                if fcntl is None:
                    fh.close()
                os.replace(tmp_path, self.leases_path)
            except (OSError, yaml.YAMLError):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise

        self.leases = new_records

    def prune_expired(self) -> int:
        """Drop all expired leases from the shared store under the file lock.

        Pre-condition: ``self.leases`` is a list.
        Post-condition: returns the count of leases that were removed
        (always ``>= 0``, never ``None``). The background lease reaper
        in ``server.py`` relies on this contract to emit accurate metrics
        and to log only when work was actually done.

        Issue #936: pruning now goes through ``_atomic_read_modify_write`` so it
        re-reads under the exclusive lock before writing. The previous
        implementation pruned a stale in-memory snapshot and called the unlocked
        ``save_leases``, clobbering leases that another process had acquired
        between this manager's last load and the prune — silently losing
        concurrent acquisitions on every reaper tick. ``_atomic_read_modify_write``
        already drops expired records before invoking the mutator, so the mutator
        is an identity function and the removed count is computed from the
        before/after record counts inside the lock.
        """
        assert isinstance(self.leases, list), "leases must be a list"
        removed_holder: list[int] = []

        def _mutate(records: list[LeaseRecord]) -> list[LeaseRecord]:
            # records is already expiry-pruned by _atomic_read_modify_write.
            return records

        before = len(self.leases)
        self._atomic_read_modify_write(_mutate, on_pruned=lambda n: removed_holder.append(n))
        removed = removed_holder[0] if removed_holder else max(0, before - len(self.leases))
        assert removed >= 0, "removed count must be non-negative"
        return removed

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
