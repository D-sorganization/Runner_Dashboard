"""Best-effort RM fast-forward, invoked by the node's systemd user timer.

No network operations run in dashboard requests. Role reads already load YAML
on every call, so a successful fast-forward needs no cache reset or restart.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.rm_reconcile import HoldReconcileError, format_hold_reconcile_diff, reconcile_holds_for_roles_dir
from staff.roles import role_validation_errors, roles_dir
from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.rm_sync")
INTERVAL_SECONDS = 15 * 60
GIT_TIMEOUT_SECONDS = 60
# The timer writes status every ~15 minutes; keep roughly a day of history.
BACKUP_KEEP = 96


def _state_path() -> Path:
    return _config_dir() / "rm_source_status.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _git(root: Path, *args: str) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=True,
    )
    return result.stdout.strip()


def _write(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Preserve prior status before atomic replacement, including error history.
        stamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S-%f")
        path.with_name(f"{path.name}.bak-{stamp}").write_bytes(path.read_bytes())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    _prune_backups(path)


def _prune_backups(path: Path) -> None:
    """Keep the newest BACKUP_KEEP status backups; stamps sort chronologically."""
    backups = sorted(path.parent.glob(f"{path.name}.bak-*"))
    for stale in backups[:-BACKUP_KEEP]:
        try:
            stale.unlink()
        except OSError as exc:
            log.warning("could not prune RM status backup %s: %s", stale.name, exc)


def source_status(root: Path | None = None, *, state_path: Path | None = None) -> dict[str, Any]:
    """Read revision/freshness without Git or network work in the request path."""
    configured = os.environ.get("STAFF_RM_ROOT")
    root = root or (Path(configured).expanduser() if configured else None)
    roles_path = (root / "staff" / "roles") if root and (root / "staff" / "roles").is_dir() else None
    validation_errs = role_validation_errors(roles_path)

    state = _read(state_path or _state_path())
    if root is None or state.get("root") != str(root.resolve()):
        return {
            "status": "not_checked",
            "commit": None,
            "commit_age_seconds": None,
            "check_age_seconds": None,
            "validation_errors": validation_errs,
        }
    now = time.time()
    return {
        **state,
        "commit_age_seconds": (max(0, int(now - state["commit_time"])) if state.get("commit_time") else None),
        "check_age_seconds": (max(0, int(now - state["checked_at"])) if state.get("checked_at") else None),
        "validation_errors": validation_errs,
    }


def refresh(root: Path, *, state_path: Path | None = None) -> dict[str, Any]:
    """Update clean main only; dirty, ahead/diverged and unavailable repos survive.

    The systemd oneshot unit serializes invocations. Operators must invoke that
    unit, not run simultaneous module processes against its clone.
    """
    root = root.expanduser().resolve()
    path = state_path or _state_path()
    previous = _read(path)
    now = time.time()
    if previous.get("root") == str(root) and 0 <= now - previous.get("checked_at", 0) < INTERVAL_SECONDS:
        _reconcile_holds_after_refresh(root, previous)
        return previous
    state: dict[str, Any] = {
        "root": str(root),
        "checked_at": now,
        "status": "unchanged",
        "commit": None,
    }
    try:
        config = os.environ.get("GIT_CONFIG_GLOBAL")
        if not config or not Path(config).expanduser().is_file():
            raise ValueError("isolated GIT_CONFIG_GLOBAL is required")
        head = _git(root, "rev-parse", "HEAD")
        if _git(root, "branch", "--show-current") != "main":
            state.update(status="skipped", reason="checkout is not on main")
        elif _git(root, "status", "--porcelain", "--untracked-files=all"):
            state.update(status="skipped", reason="checkout has local changes")
        else:
            _git(root, "fetch", "--no-tags", "origin", "main")
            target = _git(root, "rev-parse", "FETCH_HEAD")
            # Recheck after the network wait so we do not overwrite owner work.
            if (
                _git(root, "branch", "--show-current") != "main"
                or _git(root, "rev-parse", "HEAD") != head
                or _git(root, "status", "--porcelain", "--untracked-files=all")
            ):
                state.update(status="skipped", reason="checkout changed during fetch")
            elif target != head:
                base = _git(root, "merge-base", head, target)
                if base != head:
                    state.update(status="skipped", reason="main is ahead or diverged")
                else:
                    stamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S-%f")
                    backup = f"refs/staff-rm-backups/bak-{stamp}"
                    _git(root, "update-ref", backup, head)
                    _git(root, "merge", "--ff-only", target)
                    state.update(status="updated", backup_ref=backup)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Never log Git stderr/stdout: remote URLs and helpers may contain credentials.
        state.update(status="error", reason=type(exc).__name__)
    try:
        state["commit"] = _git(root, "rev-parse", "HEAD")
        state["commit_time"] = int(_git(root, "show", "-s", "--format=%ct", "HEAD"))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    _reconcile_holds_after_refresh(root, state)
    _write(path, state)
    log.info(
        "RM source refresh: %s (%s)",
        state["status"],
        state.get("reason", state.get("commit")),
    )
    return state


def _reconcile_holds_after_refresh(root: Path, state: dict[str, Any]) -> None:
    """Best-effort hold reconcile from role YAML; never raises to the timer."""
    commit = state.get("commit")
    if not commit:
        return
    roles_path = root / "staff" / "roles"
    if not roles_path.is_dir():
        roles_path = roles_dir()
    try:
        diff = reconcile_holds_for_roles_dir(roles_path, str(commit))
    except HoldReconcileError as exc:
        state["hold_reconcile"] = {"status": "error", "reason": str(exc)}
        log.error("RM hold reconcile failed (fail closed): %s", exc)
        return
    if diff is None:
        return
    state["hold_reconcile"] = {"status": "ok", "changed": diff.changed, **diff.to_dict()}
    if diff.changed:
        log.info("RM hold reconcile commit=%s %s", commit, format_hold_reconcile_diff(diff))


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Fast-forward STAFF_RM_ROOT and reconcile holds from role YAML.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print hold reconcile diff without writing staff_holds.json",
    )
    args = parser.parse_args()
    if args.dry_run:
        os.environ["STAFF_RM_RECONCILE_DRY_RUN"] = "1"
    configured = os.environ.get("STAFF_RM_ROOT")
    if not configured:
        log.warning("STAFF_RM_ROOT is unset; RM refresh skipped")
        return
    root = Path(configured)
    state = refresh(root)
    if args.dry_run and state.get("hold_reconcile"):
        sys.stdout.write(format_hold_reconcile_diff_from_state(state) + "\n")


def format_hold_reconcile_diff_from_state(state: dict[str, Any]) -> str:
    """Extract reconcile diff fields written by ``_reconcile_holds_after_refresh``."""
    keys = ("added", "deactivated", "updated", "unchanged")
    payload = {k: state["hold_reconcile"].get(k, []) for k in keys if "hold_reconcile" in state}
    return json.dumps(payload, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
