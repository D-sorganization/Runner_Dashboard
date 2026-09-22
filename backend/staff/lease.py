"""Repository_Management lease ritual, invoked as subprocesses (never imported).

check_agent_claim → post_agent_lease → agent_communicate register, and the
matching release on exit. Every step is best-effort and recorded as a run
event; only a claim held by someone else stops a run (``blocked``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from staff.store import RunStore
from staff.workspace import rm_root

LEASE_SKIPPED_NOTE = (
    "Lease ritual could not run from this node; post a `lease:` comment on the issue yourself before editing."
)


def _python_for_rm() -> str:
    return os.environ.get("STAFF_RM_PYTHON") or shutil.which("python3") or shutil.which("python") or "python"


def _run(argv: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)  # noqa: S603
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 130, str(exc)


def _claim_held(output: str) -> bool:
    compact = output.replace(" ", "").lower()
    return '"held":true' in compact or "held=true" in compact


def acquire(store: RunStore, run_id: str, *, repo: str, issue: str, agent: str, branch: str) -> str | None:
    """Run the ritual. Returns the prompt note, or None when the run is blocked."""
    root = rm_root()
    if root is None:
        store.append_event(run_id, "lease", "Repository_Management checkout not found; lease ritual skipped")
        return LEASE_SKIPPED_NOTE
    py = _python_for_rm()
    session = f"staff-{run_id}"
    rc, out = _run([py, "-m", "scripts.check_agent_claim", "--repo", repo, "--issue", issue], cwd=root)
    store.append_event(run_id, "lease", f"check_agent_claim rc={rc}: {out[:300]}")
    if _claim_held(out):
        store.update_run(run_id, status="blocked", ended_at=_now(), error="issue claim held by another agent")
        store.append_event(run_id, "blocked", "issue claim held by another agent; not starting")
        return None
    rc, out = _run(
        [
            py,
            "-m",
            "scripts.post_agent_lease",
            "--repo",
            repo,
            "--issue",
            issue,
            "--agent",
            agent,
            "--session",
            session,
        ],
        cwd=root,
    )
    store.append_event(run_id, "lease", f"post_agent_lease rc={rc}: {out[:300]}")
    rc, out = _run(
        [
            py,
            "-m",
            "scripts.agent_communicate",
            "--repo",
            repo,
            "--session",
            session,
            "register",
            "--agent",
            agent,
            "--issue",
            issue,
            "--branch",
            branch,
        ],
        cwd=root,
    )
    store.append_event(run_id, "presence", f"register rc={rc}: {out[:300]}")
    store.update_run(run_id, lease_id=session)
    return (
        f"The issue lease and presence registration were posted for you (agent={agent}, session={session}); "
        "do not post them again."
    )


def release(store: RunStore, run_id: str, *, repo: str, issue: str, agent: str) -> None:
    root = rm_root()
    current = store.get_run(run_id)
    if root is None or current is None or not current.lease_id:
        return
    py = _python_for_rm()
    session = current.lease_id
    _run([py, "-m", "scripts.agent_communicate", "--repo", repo, "--session", session, "release"], cwd=root)
    _run(
        [
            py,
            "-m",
            "scripts.release_agent_lease",
            "--repo",
            repo,
            "--issue",
            issue,
            "--agent",
            agent,
            "--session",
            session,
        ],
        cwd=root,
    )
    store.append_event(run_id, "lease", "lease and presence released")


def _now() -> str:
    from staff.store import _now as store_now  # noqa: PLC0415

    return store_now()
