#!/usr/bin/env python3
"""Reconcile armed-PR inventory and optionally trigger conflict-disarm (RD-CI-005)."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
sys.path.insert(0, _SCRIPT_DIR)
import dispatch_arm_cap as arm_cap  # noqa: E402

sys.path.remove(_SCRIPT_DIR)

logger = logging.getLogger("arm_cap_reconcile")
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def _gh_json(run: CommandRunner, args: list[str]) -> object:
    proc = run(["gh", "api", *args])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "gh api failed")
    return json.loads(proc.stdout or "null")


def _list_open_armed(repo: str, run: CommandRunner) -> list[int]:
    proc = run(
        [
            "gh",
            "api",
            f"repos/{repo}/pulls?state=open&per_page=100",
            "--jq",
            ".[] | select(.draft | not) | select(.auto_merge != null) | .number",
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "list armed failed")
    return [int(line) for line in proc.stdout.splitlines() if line.strip()]


def _fetch_snapshot(repo: str, pr: int, run: CommandRunner) -> arm_cap.PrArmSnapshot:
    raw = _gh_json(run, [f"repos/{repo}/pulls/{pr}"])
    if not isinstance(raw, dict):
        raise RuntimeError(f"unreadable PR {repo}#{pr}")
    head = raw.get("head") if isinstance(raw.get("head"), dict) else {}
    sha = str(head.get("sha") or "")
    checks: list[tuple[str, str]] = []
    if sha:
        proc = run(
            [
                "gh",
                "api",
                f"repos/{repo}/commits/{sha}/check-runs?per_page=100",
                "--jq",
                ".check_runs[] | [.name, .conclusion]",
            ]
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, list) and len(item) == 2:
                    checks.append((str(item[0]), str(item[1] or "")))
    return arm_cap.PrArmSnapshot.from_github(repo, raw, checks)


def reconcile_repo(
    repo: str,
    *,
    store: arm_cap.ArmCapStore,
    policy: arm_cap.ArmCapPolicy,
    run: CommandRunner = _default_runner,
) -> dict[str, int]:
    snapshots: list[arm_cap.PrArmSnapshot] = []
    for pr in _list_open_armed(repo, run):
        try:
            snapshots.append(_fetch_snapshot(repo, pr, run))
        except RuntimeError as exc:
            logger.warning("Skipping %s#%s during reconcile: %s", repo, pr, exc)
    return arm_cap.reconcile_snapshots(snapshots, store=store, policy=policy)


def trigger_conflict_disarm(
    repo: str,
    policy: arm_cap.ArmCapPolicy,
    *,
    dry_run: bool,
    exclude_prs: list[int] | None = None,
    run: CommandRunner = _default_runner,
) -> int:
    """Dispatch the RD conflict-disarm caller workflow (#2026 / RM #2205)."""
    inputs = arm_cap.build_disarm_workflow_inputs(policy, dry_run=dry_run, exclude_prs=exclude_prs)
    cmd = [
        "gh",
        "workflow",
        "run",
        policy.disarm_workflow,
        "--repo",
        repo,
        "-f",
        f"dry_run={inputs['dry_run']}",
        "-f",
        f"exclude_prs={inputs['exclude_prs']}",
    ]
    proc = run(cmd)
    if proc.returncode != 0:
        logger.error("conflict-disarm workflow dispatch failed: %s", proc.stderr.strip())
        return proc.returncode
    logger.info(
        "Triggered %s on %s (dry_run=%s exclude_prs=%s)",
        policy.disarm_workflow,
        repo,
        inputs["dry_run"],
        inputs["exclude_prs"],
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="D-sorganization/Runner_Dashboard", help="owner/name to scan")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--store", type=Path, default=arm_cap.DEFAULT_STORE_PATH)
    parser.add_argument("--trigger-disarm", action="store_true", help="run conflict-disarm when over cap")
    parser.add_argument("--dry-run-disarm", action="store_true", help="force dry_run on disarm workflow")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    policy = arm_cap.load_policy(args.repo_root)
    store = arm_cap.ArmCapStore(args.store)
    store.expire_stale_reservations()

    counts = reconcile_repo(args.repo, store=store, policy=policy)
    healthy = store.count_healthy_armed()
    logger.info(
        "Reconcile %s: inventory healthy=%s snapshot=%s pending_reservations=%s",
        args.repo,
        healthy,
        counts,
        store.count_pending_reservations(),
    )

    if args.trigger_disarm and arm_cap.should_trigger_cap_disarm(healthy, policy, store):
        dry = policy.shadow_mode or args.dry_run_disarm
        if policy.shadow_mode:
            logger.warning(
                "SHADOW would-disarm via %s (healthy=%s cap=%s)",
                policy.disarm_workflow,
                healthy,
                policy.max_healthy_armed,
            )
        if not policy.shadow_mode or args.dry_run_disarm:
            code = trigger_conflict_disarm(args.repo, policy, dry_run=dry)
            if code == 0 and not dry:
                arm_cap.record_disarm_triggered(store)
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
