"""Read-only repository grounding for expert panels (#2511).

A panel seat that cites code needs to read it. When a panel names ``repos``, the engine builds one
disposable snapshot of each repository's ``origin/main`` (``git archive``: tracked files only, no
``.git``, no link back to the checkout) and runs every seat with that directory as its working
directory. Seats keep their provider's read-only chat flag and get only read tools, so a snapshot
can neither be written to nor leak credentials from the checkout's git config.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tarfile
import tempfile
from contextvars import ContextVar
from pathlib import Path

from staff.cli_projects import PANEL_SCRATCH_PREFIX

log = logging.getLogger("dashboard.staff.panel_grounding")

__all__ = ["GROUNDING_DIR", "GROUNDING_REFS", "GROUNDING_TOOLS", "build_snapshot", "remove_snapshot"]

# Per-panel snapshot directory; set by ``run_panel`` so ``default_turn_runner`` keeps its signature.
GROUNDING_DIR: ContextVar[str | None] = ContextVar("panel_grounding_dir", default=None)
GROUNDING_TOOLS: tuple[str, ...] = ("view_file", "search_code", "list_files")  # keys of CHAT_READ_ONLY_TOOLS
GROUNDING_REFS: tuple[str, ...] = ("origin/main", "main")
MAX_SNAPSHOT_BYTES = 400 * 1024 * 1024
ARCHIVE_TIMEOUT_SECONDS = 120.0


def _extract_archive(root: Path, ref: str, dest: Path) -> bool:
    """Extract ``ref`` of the checkout at ``root`` into ``dest``. False if the ref is missing or too big."""
    dest.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        ["git", "-C", str(root), "archive", "--format=tar", ref],  # noqa: S607
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    total = 0
    try:
        assert proc.stdout is not None  # noqa: S101
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                total += max(member.size, 0)
                if total > MAX_SNAPSHOT_BYTES:
                    log.warning(
                        "panel grounding snapshot of %s exceeds %d bytes; skipped", root.name, MAX_SNAPSHOT_BYTES
                    )
                    proc.kill()
                    return False
                tar.extract(member, dest, filter="data")  # rejects absolute paths, links out, devices
        return proc.wait(timeout=ARCHIVE_TIMEOUT_SECONDS) == 0
    except (tarfile.TarError, OSError, subprocess.TimeoutExpired) as exc:
        log.warning("panel grounding snapshot of %s@%s failed: %s", root.name, ref, exc)
        proc.kill()
        return False
    finally:
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()


def build_snapshot(repos: dict[str, Path]) -> str | None:
    """Snapshot each repo's origin/main (else main) under one new directory; return it, or None if none worked.

    Pre: ``repos`` maps fleet repository names to local checkouts.
    Post: ``<dir>/<repo>/...`` holds tracked files only; repos that could not be snapshotted are absent.
    The caller owns the directory and must pass it to :func:`remove_snapshot`.
    """
    base = Path(tempfile.mkdtemp(prefix=PANEL_SCRATCH_PREFIX))
    built = 0
    for name, root in repos.items():
        for ref in GROUNDING_REFS:
            dest = base / name
            if _extract_archive(root, ref, dest):
                built += 1
                break
            shutil.rmtree(dest, ignore_errors=True)
    if built == 0:
        shutil.rmtree(base, ignore_errors=True)
        return None
    return str(base)


def remove_snapshot(path: str | None) -> None:
    """Delete a snapshot directory; never raises."""
    if path:
        shutil.rmtree(path, ignore_errors=True)
