"""Worktree and merged-branch lifecycle hygiene for Staff runs (#2269).

Persists run transcripts outside ephemeral git worktrees, removes finished clean
worktrees on run exit, and prunes ancestor-merged local/remote branches.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from staff import cli_projects
from staff.worktree_links import (
    _is_junction_path,
    _link_kind,
    link_refusal_reason,
    refuse_worktree_links,
    unlink_worktree_links,
)

log = logging.getLogger("dashboard.staff.worktree_hygiene")


_FILE_ATTRIBUTE_DIRECTORY = getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10)


@dataclass(frozen=True)
class _TrackedSymlinkSnapshot:
    rel_posix: str
    kind: str
    target: str
    target_is_directory: bool


def persist_run_transcript(store: Any, rec_id: str, workdir: Path) -> Path | None:
    """Atomically persist bytes and verify their run association before cleanup."""
    src = workdir / ".staff" / "transcript.log"
    if not src.is_file():
        return None
    db_path = getattr(store, "path", None) or getattr(store, "db_path", None)
    base_dir = Path(db_path).parent if db_path else (Path.home() / ".config" / "runner-dashboard")
    dst_dir = base_dir / "transcripts"
    temporary: Path | None = None
    try:
        if store.get_run(rec_id) is None or Path(rec_id).name != rec_id:
            return None
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{rec_id}.log"
        if dst.resolve().is_relative_to(workdir.resolve()):
            return None
        with tempfile.NamedTemporaryFile(dir=dst_dir, delete=False) as output:
            temporary = Path(output.name)
            with src.open("rb") as source:
                shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, dst)
        if os.name != "nt":
            directory = os.open(dst_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        if not _same_content(src, dst):
            return None
        store.update_run(rec_id, transcript_path=str(dst))
        record = store.get_run(rec_id)
        return dst if record is not None and record.transcript_path == str(dst) else None
    except Exception as exc:  # noqa: BLE001 — any persistence failure must preserve the source
        log.warning("failed to persist transcript for %s: %s", rec_id, exc)
        return None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("temporary transcript retained at %s: %s", temporary, exc)


def _same_content(source: Path, destination: Path) -> bool:
    with source.open("rb") as left, destination.open("rb") as right:
        return hashlib.file_digest(left, "sha256").digest() == hashlib.file_digest(right, "sha256").digest()


def _path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _has_links(path: Path) -> bool:
    return link_refusal_reason(path) is not None


def _pid_is_running(pid: int) -> bool:
    import psutil  # noqa: PLC0415 — minimal tooling imports must not require runtime probes

    return psutil.pid_exists(pid)


def _git_in_worktree(worktree: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(worktree), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _posix_rel(p: str) -> str:
    return p.replace("\\", "/").strip()


def _path_under_prefix(rel: str, prefix: str) -> bool:
    rel_n, pre_n = _posix_rel(rel), _posix_rel(prefix)
    if not pre_n:
        return False
    return rel_n == pre_n or rel_n.startswith(pre_n + "/")


def _gitmodules_submodule_paths(worktree: Path) -> frozenset[str]:
    """Paths declared in ``.gitmodules`` (forward slashes), empty when missing or unparseable."""
    gm = worktree / ".gitmodules"
    if not gm.is_file():
        return frozenset()
    paths: set[str] = set()
    try:
        text = gm.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    for raw in text.splitlines():
        line = raw.strip()
        if not line.lower().startswith("path"):
            continue
        if "=" not in line:
            continue
        val = line.split("=", 1)[1].strip().strip('"').strip("'")
        if val:
            paths.add(_posix_rel(val))
    return frozenset(paths)


def _safe_force_untracked_allow_prefixes(worktree: Path) -> frozenset[str]:
    """Prefixes where untracked/ignored debris alone may be removed with ``--force`` (#4142 follow-up)."""
    allow = {".staff", ".gemini", ".venv"}
    allow.update(_gitmodules_submodule_paths(worktree))
    return frozenset(allow)


def _untracked_paths_for_safe_force_scan(worktree: Path) -> list[str]:
    """List relative paths that are untracked or ignored-from-standard (both matter for evidence)."""
    out: list[str] = []
    for extra in (
        ("ls-files", "-z", "--others", "--exclude-standard"),
        ("ls-files", "-z", "--others", "-i", "--exclude-standard"),
    ):
        res = _git_in_worktree(worktree, *extra, timeout=60)
        if res.returncode != 0:
            return [f"<git ls-files failed: {res.stderr.strip() or res.stdout}>"]
        if not res.stdout:
            continue
        # NUL-separated; tolerate trailing NUL
        for part in res.stdout.split("\0"):
            rel = part.strip()
            if rel:
                out.append(_posix_rel(rel))
    return out


def _rel_touches_disposable_in_tree_link(worktree: Path, rel: str) -> bool:
    """True when ``rel`` names a link entry or a path under an in-tree link (incl. Windows junctions)."""
    parts = rel.replace("\\", "/").split("/")
    for end in range(1, len(parts) + 1):
        try:
            if _link_kind(worktree.joinpath(*parts[:end])):
                return True
        except OSError:
            return False
    return False


def _untracked_outside_safe_force_allowlist(worktree: Path) -> bool:
    """True when any untracked/ignored path lies outside submodule paths and staff transcript dirs."""
    prefixes = _safe_force_untracked_allow_prefixes(worktree)
    for rel in _untracked_paths_for_safe_force_scan(worktree):
        if rel.startswith("<git "):
            return True  # refuse force when git state is unreadable
        if not any(_path_under_prefix(rel, p) for p in prefixes) and not _rel_touches_disposable_in_tree_link(
            worktree, rel
        ):
            return True
    return False


def _index_tracked_symlink_paths(worktree: Path) -> list[str]:
    """Relative POSIX paths for stage-zero index entries with symlink mode (120000)."""
    res = _git_in_worktree(worktree, "ls-files", "-s", "-z")
    if res.returncode != 0:
        raise OSError(res.stderr.strip() or "git ls-files failed")
    paths: list[str] = []
    for chunk in res.stdout.split("\0"):
        if not chunk or "\t" not in chunk:
            continue
        meta, path = chunk.split("\t", 1)
        if meta.split()[0] != "120000":
            continue
        paths.append(_posix_rel(path))
    return paths


def _classify_tracked_link(entry: Path, raw_kind: str) -> tuple[str, bool]:
    """Return journal kind (``junction`` or ``symlink``) and directory semantics."""
    if raw_kind == "Windows reparse point":
        if _is_junction_path(entry):
            return "junction", True
        attributes = getattr(entry.lstat(), "st_file_attributes", 0)
        return "symlink", bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
    if raw_kind == "symlink":
        return "symlink", entry.is_dir()
    raise OSError(f"unsupported tracked link kind: {raw_kind}")


def _capture_tracked_symlink_journal(worktree: Path) -> tuple[_TrackedSymlinkSnapshot, ...]:
    """Snapshot tracked symlinks before unlink; refuse when they cannot be reconstructed."""
    snapshots: list[_TrackedSymlinkSnapshot] = []
    for rel in _index_tracked_symlink_paths(worktree):
        entry = worktree.joinpath(*rel.split("/"))
        try:
            raw_kind = _link_kind(entry)
            if not raw_kind:
                raise OSError(f"tracked path is not a link entry: {rel}")
            target = os.readlink(entry)
            kind, target_is_directory = _classify_tracked_link(entry, raw_kind)
        except OSError as exc:
            raise OSError(f"cannot snapshot tracked symlink {rel}: {exc}") from exc
        snapshots.append(
            _TrackedSymlinkSnapshot(
                rel_posix=rel,
                kind=kind,
                target=target,
                target_is_directory=target_is_directory,
            )
        )
    return tuple(snapshots)


def _snapshot_expects_junction(snap: _TrackedSymlinkSnapshot) -> bool:
    return snap.kind == "junction" or (snap.kind == "Windows reparse point" and snap.target_is_directory)


def _restored_link_matches_snapshot(entry: Path, snap: _TrackedSymlinkSnapshot) -> bool:
    try:
        if os.readlink(entry) != snap.target:
            return False
    except OSError:
        return False
    if _snapshot_expects_junction(snap):
        return _is_junction_path(entry)
    if snap.target_is_directory:
        return entry.is_dir() and not _is_junction_path(entry)
    return not entry.is_dir()


def _create_windows_junction(link: Path, target: str) -> None:
    """Create a directory junction on Windows (never a directory symlink)."""
    if os.name != "nt":
        raise OSError(f"cannot create junction on non-Windows platform: {link}")
    target_text = os.fsdecode(target)
    link_text = os.fsdecode(link)
    primary_error: OSError | None = None
    try:
        import _winapi  # noqa: PLC0415 — Windows-only junction primitive

        create_junction = getattr(_winapi, "CreateJunction", None)
        if create_junction is None:
            raise OSError("CreateJunction is unavailable")
        create_junction(target_text, link_text)
        return
    except (ImportError, AttributeError, OSError) as exc:
        primary_error = OSError(str(exc)) if not isinstance(exc, OSError) else exc
    result = subprocess.run(  # noqa: S603
        ["cmd", "/c", "mklink", "/J", link_text, target_text],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "mklink /J failed").strip()
        raise OSError(f"junction creation failed for {link}: {detail}") from primary_error
    if not link.exists():
        raise OSError(f"junction creation reported success but {link} is missing")


def _recreate_tracked_link_entry(entry: Path, snap: _TrackedSymlinkSnapshot) -> None:
    """Recreate a journal entry using the recorded kind and directory semantics."""
    if _snapshot_expects_junction(snap):
        _create_windows_junction(entry, snap.target)
        if not entry.exists():
            raise OSError(f"junction missing after recreate: {entry}")
        if os.name == "nt" and not _is_junction_path(entry):
            raise OSError(f"recreate changed link kind at {entry}: expected junction")
        return
    if snap.target_is_directory:
        entry.symlink_to(snap.target, target_is_directory=True)
    else:
        entry.symlink_to(snap.target)
    if not entry.is_symlink() and not entry.exists():
        raise OSError(f"symlink missing after recreate: {entry}")


def _ensure_real_parent_dirs(worktree: Path, rel_posix: str) -> None:
    """Post: every parent of ``rel_posix`` is a real directory inside ``worktree``.

    Missing parents are created one level at a time. A link or non-directory
    occupant raises ``OSError`` so rollback never writes through it.
    """
    current = worktree
    for part in rel_posix.split("/")[:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            continue
        if _link_kind(current) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError(f"parent is not a real directory: {current}")


def _restore_tracked_symlinks_from_journal(
    worktree: Path,
    journal: tuple[_TrackedSymlinkSnapshot, ...],
    git_cwd: Path,
) -> list[str]:
    """Restore index-tracked symlinks removed during a failed disposal attempt.

    Pre: ``journal`` was captured from this worktree before unlinking.
    Post: each journal entry that is missing is recreated with its recorded raw
    target; an existing entry (same or different) and every link target are left
    untouched. Returns the relative paths that could not be restored.
    """
    if not journal or not worktree.exists() or not _registered_worktree_unlocked(git_cwd, worktree):
        return []
    failures: list[str] = []
    for snap in journal:
        entry = worktree.joinpath(*snap.rel_posix.split("/"))
        try:
            try:
                kind = _link_kind(entry)
            except OSError:
                kind = ""
            if kind:
                if _restored_link_matches_snapshot(entry, snap):
                    continue
                failures.append(snap.rel_posix)
                log.warning(
                    "rollback preserved concurrent link at %s under %s",
                    snap.rel_posix,
                    worktree,
                )
                continue
            if entry.exists():
                failures.append(snap.rel_posix)
                log.warning(
                    "rollback refused non-link occupant at %s under %s",
                    snap.rel_posix,
                    worktree,
                )
                continue
            _ensure_real_parent_dirs(worktree, snap.rel_posix)
            _recreate_tracked_link_entry(entry, snap)
            log.info(
                "restored tracked %s %s -> %s",
                snap.kind,
                snap.rel_posix,
                snap.target,
            )
        except OSError as exc:
            failures.append(snap.rel_posix)
            log.warning("rollback failed for %s under %s: %s", snap.rel_posix, worktree, exc)
    if worktree.exists():
        _git_in_worktree(worktree, "update-index", "--refresh")
    return failures


def _worktree_tracked_clean_vs_head(worktree: Path) -> bool:
    """True when index and tracked tree match HEAD (submodule pointers unchanged vs HEAD)."""
    diff_files = _git_in_worktree(worktree, "diff-files", "--quiet")
    diff_index = _git_in_worktree(worktree, "diff-index", "--quiet", "HEAD", "--")
    return diff_files.returncode == 0 and diff_index.returncode == 0


def _eligible_for_safe_force_removal(worktree: Path) -> bool:
    """``git worktree remove --force`` only when tracked clean and no non-disposable untracked paths."""
    if not _worktree_tracked_clean_vs_head(worktree):
        return False
    if _untracked_outside_safe_force_allowlist(worktree):
        return False
    return True


def _stderr_suggests_safe_force(stderr: str) -> bool:
    """Heuristic: git refused removal due to extra paths, not necessarily dirty tracked state."""
    lower = stderr.lower()
    needles = (
        "modified or untracked files",
        "untracked files",
        "contains modified or untracked files",
        "cannot remove worktree",
    )
    return any(n in lower for n in needles)


def _registered_worktree_unlocked(checkout: Path, worktree: Path) -> bool:
    """Require a registered, unlocked tree before unlinking tracked entries."""
    result = _git_out(checkout, "worktree", "list", "--porcelain", "-z")
    if result.returncode:
        return False
    for record in result.stdout.split("\0\0"):
        fields = record.split("\0")
        if fields[0].startswith("worktree ") and _path_key(fields[0][9:]) == _path_key(worktree):
            return not any(field == "locked" or field.startswith("locked ") for field in fields)
    return False


def _rollback_tracked_symlinks_best_effort(
    worktree: Path,
    journal: tuple[_TrackedSymlinkSnapshot, ...],
    git_cwd: Path,
) -> None:
    """Roll back a failed disposal attempt without hiding the original failure.

    Pre: the attempt did not confirm removal. Post: when the registered worktree
    survives, missing journal entries are restored; a rollback step that fails
    is logged with the worktree path and never raised (#4360).
    """
    try:
        if not worktree.exists():
            return  # Git removed the tree; never recreate it or its registration.
        failures = _restore_tracked_symlinks_from_journal(worktree, journal, git_cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("tracked symlink rollback incomplete for %s: %s", worktree, exc)
        return
    if failures:
        log.warning("tracked symlink rollback left %s unrestored under %s", ", ".join(failures), worktree)


def _prune_worktrees_after_removal_attempt(git_cwd: Path, worktree: Path) -> None:
    """Prune once removal/rollback is settled; a prune failure cannot change that outcome."""
    try:
        _git_out(git_cwd, "worktree", "prune")
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("worktree prune after removal attempt failed for %s: %s", worktree, exc)


def _try_remove_git_worktree(worktree: Path, checkout: Path | None) -> tuple[bool, subprocess.CompletedProcess[str]]:
    """Run ``git worktree remove`` from ``checkout`` when provided, else from ``worktree``."""
    git_cwd = checkout if checkout is not None else worktree
    if _has_links(worktree):
        if not _registered_worktree_unlocked(git_cwd, worktree) or not _eligible_for_safe_force_removal(worktree):
            return False, subprocess.CompletedProcess([], 1, "", "worktree is not disposable")
        try:
            journal = _capture_tracked_symlink_journal(worktree)
        except OSError as exc:
            return False, subprocess.CompletedProcess([], 1, "", str(exc))
        result = subprocess.CompletedProcess(["worktree", "remove", "--force"], 1, "", "")
        removed = False
        try:
            unlink_worktree_links(worktree)
            result = _git_out(git_cwd, "worktree", "remove", "--force", str(worktree), timeout=60)
            removed = result.returncode == 0 and not worktree.exists()
        except OSError as exc:
            return False, subprocess.CompletedProcess([], 1, "", str(exc))
        finally:
            # Every exit without confirmed removal (nonzero, timeout, partial unlink,
            # interruption, unexpected error) rolls back before anything propagates.
            if not removed:
                _rollback_tracked_symlinks_best_effort(worktree, journal, git_cwd)
        _prune_worktrees_after_removal_attempt(git_cwd, worktree)
        return removed, result
    result = _git_out(git_cwd, "worktree", "remove", str(worktree), timeout=60)
    if result.returncode == 0 and not worktree.exists():
        return True, result
    if (
        result.returncode != 0
        and _eligible_for_safe_force_removal(worktree)
        and _stderr_suggests_safe_force(result.stderr)
    ):
        forced = _git_out(git_cwd, "worktree", "remove", "--force", str(worktree), timeout=60)
        _git_out(git_cwd, "worktree", "prune")
        if forced.returncode == 0 and not worktree.exists():
            return True, forced
    return False, result


def remove_worktree_checked(
    worktree: Path, checkout: Path | None, branch: str, preserved_transcript: Path | None = None
) -> bool:
    """Remove a finished staff worktree; may use ``--force`` only when safe (#submodule leak)."""
    from staff import workspace  # noqa: PLC0415

    try:
        root = workspace.staff_worktrees_root()
        if refuse_worktree_links(worktree):
            return False
        if (worktree / ".git").exists():
            if _path_key(worktree.parent) != _path_key(root):
                return False
            if (worktree / ".staff").exists() and _has_links(worktree / ".staff"):
                return False
            source = worktree / ".staff" / "transcript.log"
            if source.exists() and (
                preserved_transcript is None
                or preserved_transcript.resolve().is_relative_to(worktree.resolve())
                or not _same_content(source, preserved_transcript)
            ):
                return False
            ok, _result = _try_remove_git_worktree(worktree, checkout)
            if not ok:
                return False
        else:
            worktree.rmdir()  # Only empty non-Git scratch directories are disposable.
        try:
            workspace.prune_worktree_branch(checkout, branch)
            if _path_key(worktree.parent) == _path_key(root):
                cli_projects.remove_worktree_project(worktree, root, os.environ)
        except Exception as exc:  # noqa: BLE001 — secondary cleanup cannot undo confirmed removal
            log.warning("worktree removed but administrative cleanup failed for %s: %s", worktree, exc)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("worktree removal preserved %s: %s", worktree, exc)
        return False


def _cleanup_finished_worktree(store: Any, rec_id: str, workdir: Path, repo: str | None, branch: str = "") -> bool:
    """Pre: terminal owned run, clean direct child, no live PID. Post: evidence survives refusal."""
    from staff import workspace  # noqa: PLC0415

    try:
        record = store.get_run(rec_id)
        if record is None or _path_key(record.workdir) != _path_key(workdir):
            return False
        if record.status not in {"succeeded", "failed", "cancelled"} or (record.pid and _pid_is_running(record.pid)):
            return False
        if record.failure_class == "orphaned":
            return False  # Attempted termination is not proof that all descendants stopped.
        root = workspace.staff_worktrees_root()
        if (
            _path_key(workdir.parent) != _path_key(root)
            or workdir.name == "_scratch"
            or not (workdir / ".git").is_file()
        ):
            if not _has_links(workdir / ".staff"):
                persist_run_transcript(store, rec_id, workdir)
            return False  # Owned cloud/scratch runs retain logging without becoming cleanup candidates.
        if refuse_worktree_links(workdir, store, rec_id):
            return False
        if workspace.worktree_has_unpushed_commits(workdir):
            store.append_event(rec_id, "worktree", f"unpushed worktree preserved at {workdir}")
            return False
        if _has_links(workdir / ".staff"):
            return False
        saved = persist_run_transcript(store, rec_id, workdir)
        if saved is None or not _same_content(workdir / ".staff" / "transcript.log", saved):
            return False
        workspace.remove_worktree(
            workdir,
            checkout=workspace.find_repo_checkout(repo) if repo else None,
            branch=branch,
            preserved_transcript=saved,
        )
        return not workdir.exists()
    except Exception as exc:  # noqa: BLE001 — uncertainty at the destructive boundary preserves data
        log.warning("finished worktree preserved at %s: %s", workdir, exc)
        return False


def _git_out(cwd: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _active_worktree_branches(checkout: Path) -> set[str]:
    res = _git_out(checkout, "worktree", "list", "--porcelain")
    if res.returncode != 0:
        return set()
    branches: set[str] = set()
    for line in res.stdout.splitlines():
        if line.startswith("branch refs/heads/"):
            branches.add(line.removeprefix("branch refs/heads/").strip())
    return branches


def prune_merged_branches(checkout: Path, *, delete_remote_merged: bool = False) -> dict[str, int]:
    """Prune stale worktree refs, local merged/gone branches, and optionally merged remote staff/* branches."""
    if not checkout.exists() or not (checkout / ".git").exists():
        return {"local_deleted": 0, "remote_deleted": 0}
    try:
        _git_out(checkout, "fetch", "origin", "--prune", "--quiet", timeout=60)
        _git_out(checkout, "worktree", "prune")
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch/worktree prune skipped on %s: %s", checkout, exc)

    head_res = _git_out(checkout, "symbolic-ref", "--quiet", "--short", "HEAD")
    head_branch = head_res.stdout.strip() if head_res.returncode == 0 else ""
    protected = {"main", "master", "HEAD", ""} | _active_worktree_branches(checkout)
    if head_branch:
        protected.add(head_branch)

    to_delete: set[str] = set()
    merged_res = _git_out(checkout, "branch", "--merged", "origin/main", "--format=%(refname:short)")
    if merged_res.returncode == 0:
        for line in merged_res.stdout.splitlines():
            b = line.strip()
            if b and b not in protected:
                to_delete.add(b)

    refs_res = _git_out(checkout, "for-each-ref", "--format=%(refname:short)|%(upstream:track)", "refs/heads")
    if refs_res.returncode == 0:
        for line in refs_res.stdout.splitlines():
            if "|" not in line:
                continue
            b, track = line.split("|", 1)
            b = b.strip()
            if b in protected or b in to_delete:
                continue
            if ": gone]" in track:
                ahead = _git_out(checkout, "rev-list", f"origin/main..{b}", "--count")
                if ahead.returncode == 0 and ahead.stdout.strip() == "0":
                    to_delete.add(b)

    local_deleted = 0
    for b in sorted(to_delete):
        del_res = _git_out(checkout, "branch", "-D", b)
        if del_res.returncode == 0:
            local_deleted += 1

    remote_deleted = 0
    if delete_remote_merged:
        rm_res = _git_out(checkout, "branch", "-r", "--merged", "origin/main", "--format=%(refname:short)")
        if rm_res.returncode == 0:
            for line in rm_res.stdout.splitlines():
                rb = line.strip()
                if not rb.startswith("origin/staff/"):
                    continue
                short_rb = rb.removeprefix("origin/")
                push_res = _git_out(checkout, "push", "origin", "--delete", short_rb, timeout=60)
                if push_res.returncode == 0:
                    remote_deleted += 1

    return {"local_deleted": local_deleted, "remote_deleted": remote_deleted}


def finalize_run_worktree(
    store: Any,
    rec_id: str,
    workdir: Path | None,
    repo: str | None,
    *,
    branch: str = "",
) -> None:
    """Persist transcript and remove clean finished git worktrees under ``staff_worktrees_root``."""
    if workdir is not None and workdir.exists():
        _cleanup_finished_worktree(store, rec_id, workdir, repo, branch)


def prune_worktree_branch(checkout: Path | None, branch: str = "") -> None:
    """Prune stale worktree administrative entries and delete ``branch`` if merged into origin/main."""
    if checkout is None or not checkout.exists():
        return
    try:
        _git_out(checkout, "worktree", "prune")
        if branch and branch not in ("main", "master", "HEAD"):
            ahead = _git_out(checkout, "rev-list", f"origin/main..{branch}", "--count")
            if ahead.returncode == 0 and ahead.stdout.strip() == "0":
                _git_out(checkout, "branch", "-D", branch)
    except Exception as exc:  # noqa: BLE001
        log.debug("prune_worktree_branch skipped on %s: %s", checkout, exc)


def sweep_stale_worktrees(
    store: Any = None,
    *,
    active_workdirs: set[str] | None = None,
) -> dict[str, int]:
    """Prune finished clean git worktrees under ``staff_worktrees_root`` (#2538)."""
    from staff import workspace  # noqa: PLC0415

    wt_root = workspace.staff_worktrees_root()
    if not wt_root.is_dir():
        return {"removed": 0, "preserved_unpushed": 0, "skipped_active": 0, "preserved_links": 0}

    empty = {"removed": 0, "preserved_unpushed": 0, "skipped_active": 0, "preserved_links": 0}
    try:
        active_norm = {_path_key(p) for p in (active_workdirs or set()) if p}
        active_norm.update(_path_key(rec.workdir) for rec in store.active_runs() if rec.workdir)
    except Exception as exc:  # noqa: BLE001 — no authoritative active exclusion means no cleanup
        log.warning("worktree sweep preserved all candidates: %s", exc)
        return empty

    removed = 0
    preserved_unpushed = 0
    skipped_active = 0
    preserved_links = 0
    for child in sorted(wt_root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name == "_scratch":
            continue
        if not (child / ".git").exists():
            continue
        if _path_key(child) in active_norm:
            skipped_active += 1
            continue
        if "-run-" not in child.name:
            continue
        repo_prefix, suffix = child.name.rsplit("-run-", 1)
        if refuse_worktree_links(child, store, "run-" + suffix):
            preserved_links += 1
            continue
        if workspace.worktree_has_unpushed_commits(child):
            preserved_unpushed += 1
            continue
        removed += int(_cleanup_finished_worktree(store, "run-" + suffix, child, repo_prefix))
    return {
        "removed": removed,
        "preserved_unpushed": preserved_unpushed,
        "skipped_active": skipped_active,
        "preserved_links": preserved_links,
    }
