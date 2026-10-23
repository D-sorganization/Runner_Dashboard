"""Admission for security scans (#2572, panel verdict q4 items 1 and 6).

:func:`admit` is a pure function of the operator config and a snapshot of facts
gathered by the executor (:class:`Facts`). It returns every reason a real scan
must not start; an empty list admits. The executor calls it before phase A and
again before phase B, so conditions that change mid-run (CI queue, disk) are
rechecked.

A real scan needs:

- a scan host declared ``isolation: "vm"``, reached by ``DOCKER_HOST``, with a
  filtered build network. ControlTower is never a scan host.
- an enabled repository, a valid ``.oss-scanner/`` contract, and the
  default-branch head SHA
- at least ``min_free_gb`` free disk and an idle CI queue
- the night window, the single fleet-wide slot, and budget left today
- a pinned CLI version and a live subscription login

A dry run evaluates the same rules but never executes, so it is allowed anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from vuln_scan.operator_config import OperatorConfig, ScanHost

FORBIDDEN_HOST_MARKERS = ("controltower",)


@dataclass(frozen=True)
class Facts:
    """What the executor observed; every field defaults to the unsafe answer."""

    now: datetime
    host: ScanHost | None = None
    contract_valid: bool = False
    default_branch_sha: str | None = None
    free_gb: float | None = None
    queued_jobs: int | None = None
    slot_free: bool = False
    spent_usd_today: float = 0.0
    oauth_ok: bool = False


def in_window(now: datetime, start: str, end: str, tz: str) -> bool:
    try:
        local = now.astimezone(ZoneInfo(tz))
    except ZoneInfoNotFoundError:
        return False
    lo, hi = time.fromisoformat(start), time.fromisoformat(end)
    t = local.time()
    return lo <= t < hi if lo <= hi else (t >= lo or t < hi)


def host_problems(host: ScanHost | None) -> list[str]:
    if host is None:
        return ["no scan host is configured"]
    problems: list[str] = []
    text = f"{host.name} {host.docker_host or ''}".lower()
    if any(marker in text for marker in FORBIDDEN_HOST_MARKERS):
        problems.append(f"{host.name} is ControlTower, which never runs scans")
    if not host.eligible_for_real_scans:
        problems.append(
            f"{host.name} is isolation {host.isolation!r}; real scans need 'vm'"
        )
    if not host.docker_host:
        problems.append(f"{host.name} has no docker_host (ssh://scan-vm)")
    if not host.build_network:
        problems.append(f"{host.name} has no filtered build_network")
    return problems


def admit(config: OperatorConfig, repo: str, sha: str, facts: Facts) -> list[str]:
    """Every reason this scan may not run now (empty = admitted)."""
    reasons: list[str] = []
    if config.errors:
        reasons.append("operator config has errors: " + "; ".join(config.errors))
    if not config.repo(repo).enabled:
        reasons.append(f"{repo} is not enabled in config/security_scan.json")
    if not facts.contract_valid:
        reasons.append(f"{repo} has no valid .oss-scanner/ contract")
    if not facts.default_branch_sha or sha != facts.default_branch_sha:
        reasons.append("only the default-branch head SHA may be scanned")
    reasons.extend(host_problems(facts.host))
    if facts.free_gb is None or facts.free_gb < config.min_free_gb:
        reasons.append(
            f"scan host free disk {facts.free_gb} GB < {config.min_free_gb} GB"
        )
    if facts.queued_jobs is None or facts.queued_jobs > 0:
        reasons.append(f"CI queue is not idle ({facts.queued_jobs} queued)")
    if not in_window(
        facts.now, config.window_start, config.window_end, config.timezone
    ):
        reasons.append(
            f"outside the {config.window_start}-{config.window_end} "
            f"{config.timezone} window"
        )
    if not facts.slot_free:
        reasons.append("the fleet-wide scan slot is busy")
    if facts.spent_usd_today >= config.usd_per_day:
        reasons.append(
            f"daily budget spent ({facts.spent_usd_today:.2f} "
            f"of {config.usd_per_day:.2f} USD)"
        )
    if not config.executor.cli_version:
        reasons.append("executor.cli_version is not pinned")
    if not facts.oauth_ok:
        reasons.append("the Claude subscription login is not live on this node")
    return reasons


def runtime_problems(config: OperatorConfig, facts: Facts) -> list[str]:
    """The subset rechecked while a scan runs (queue, disk); non-empty = abort."""
    problems: list[str] = []
    if facts.queued_jobs is None or facts.queued_jobs > 0:
        problems.append(f"CI jobs queued ({facts.queued_jobs}); yielding to CI")
    if facts.free_gb is not None and facts.free_gb < config.min_free_gb:
        problems.append(f"scan host free disk fell to {facts.free_gb} GB")
    return problems
