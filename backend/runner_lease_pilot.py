"""Read-only staging pilot for the runner-lease YAML->SQLite cutover (#3878).

Gathers the "spoke pilot evidence" exit criterion without touching a node's
live claim store: the node's ``leases.yml`` is copied into a scratch
directory, and the #3877 ``LeaseMigrationHarness`` drives that copy (primary)
against a scratch ``SqliteLeaseStore`` (secondary) through dual-read, drain,
cutover and rollback. The report carries counts and verdicts only, never
lease contents, so it can be pasted onto the issue as evidence.

Precondition: ``work_dir`` is not ``source_config_dir`` itself. A
subdirectory is fine and is what a node needs, because ``LeaseManager`` only
loads files under the allowed config roots (``~/.config/runner-dashboard``).
Postcondition: the source ``leases.yml`` is byte-for-byte unchanged (checked
and reported as ``source_unchanged``). No production cutover is wired here;
that still needs the separately recorded sign-off required by #3874.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import shutil
import time
from pathlib import Path
from typing import Any

import yaml
from runner_lease import LeaseManager, LeaseRecord
from runner_lease_migration import LeaseMigrationHarness, YamlLeaseStore
from runner_lease_sqlite import SqliteLeaseStore

log = logging.getLogger("dashboard.runner_lease_pilot")

_LEASES_FILE = "leases.yml"
# Fields that must survive a drain. fencing_token is excluded on purpose:
# SqliteLeaseStore.write_all restarts every lease's sequence at 0.
_COMPARED_FIELDS = ("principal_id", "acquired_at", "expires_at", "task_id", "metadata")


@dataclasses.dataclass(frozen=True)
class PilotReport:
    source_record_count: int
    copy_record_count: int
    agrees_before_drain: bool
    drained_record_count: int
    agrees_after_drain: bool
    field_mismatches: tuple[str, ...]
    cutover_ok: bool
    rollback_ok: bool
    sqlite_journal_mode: str
    source_unchanged: bool

    @property
    def passed(self) -> bool:
        return (
            self.copy_record_count == self.source_record_count
            and self.agrees_after_drain
            and not self.field_mismatches
            and self.drained_record_count == self.source_record_count
            and self.cutover_ok
            and self.rollback_ok
            and self.sqlite_journal_mode == "wal"
            and self.source_unchanged
        )

    def as_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["field_mismatches"] = list(self.field_mismatches)
        data["passed"] = self.passed
        return data


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _count_active_source_leases(path: Path) -> int:
    """Active leases in the source file, parsed independently of ``LeaseManager``.

    ``LeaseManager.load_leases`` logs and yields ``[]`` when it rejects a file,
    so the pilot cross-checks the copy it loaded against this count.
    """
    if not path.exists():
        return 0
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    now = time.time()
    return sum(1 for rec in data.get("leases") or [] if rec.get("expires_at") is None or rec["expires_at"] > now)


def _field_mismatches(expected: list[LeaseRecord], actual: list[LeaseRecord]) -> tuple[str, ...]:
    actual_by_runner = {record.runner_id: record for record in actual}
    mismatches = []
    for record in sorted(expected, key=lambda r: r.runner_id):
        other = actual_by_runner.get(record.runner_id)
        if other is None:
            continue  # a missing runner is already reported by the dual-read
        for field in _COMPARED_FIELDS:
            if getattr(record, field) != getattr(other, field):
                mismatches.append(f"{record.runner_id}: {field}")
    return tuple(mismatches)


def run_pilot(source_config_dir: Path, work_dir: Path) -> PilotReport:
    """Run dual-read -> drain -> cutover -> rollback on a copy of a node's leases."""
    source_config_dir = source_config_dir.resolve()
    work_dir = work_dir.resolve()
    if work_dir == source_config_dir:
        raise ValueError(f"work_dir must not be the source config dir itself: {work_dir}")

    source_leases = source_config_dir / _LEASES_FILE
    digest_before = _digest(source_leases)
    source_count = _count_active_source_leases(source_leases)
    copy_dir = work_dir / "yaml-copy"
    copy_dir.mkdir(parents=True, exist_ok=True)
    if source_leases.exists():
        shutil.copyfile(source_leases, copy_dir / _LEASES_FILE)

    primary = YamlLeaseStore(LeaseManager(config_dir=copy_dir))
    secondary = SqliteLeaseStore(work_dir / "pilot-leases.sqlite3")
    try:
        harness = LeaseMigrationHarness(primary, secondary)
        copy_records = list(primary.read_all())
        before = harness.read_dual()
        drain = harness.drain()
        after = harness.read_dual()
        mismatches = _field_mismatches(copy_records, list(after.secondary_records))

        harness.cutover(drain.fencing_token)
        cutover_ok = harness.authority == "secondary" and harness.authoritative_store() is secondary
        harness.rollback()
        rollback_ok = harness.authority == "primary" and harness.authoritative_store() is primary

        journal_mode = secondary.journal_mode()
    finally:
        secondary.close()

    report = PilotReport(
        source_record_count=source_count,
        copy_record_count=len(copy_records),
        agrees_before_drain=before.agrees,
        drained_record_count=drain.record_count,
        agrees_after_drain=after.agrees,
        field_mismatches=mismatches,
        cutover_ok=cutover_ok,
        rollback_ok=rollback_ok,
        sqlite_journal_mode=journal_mode,
        source_unchanged=_digest(source_leases) == digest_before,
    )
    log.info("runner_lease pilot: passed=%s records=%d", report.passed, report.source_record_count)
    return report
