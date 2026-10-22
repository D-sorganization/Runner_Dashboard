"""Filesystem and service transaction helpers for one auto-deploy switch."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from node_auto_deploy.releases import release_root

TransactionResult = Literal["success", "skipped", "failed", "rollback", "rollback_failed"]


class UnsafeDeploymentPathError(ValueError):
    """A deployment target or mutable path escapes the trusted release layout."""


@dataclass(frozen=True)
class SwitchReceipt:
    previous_target: Path | None
    previous_was_plain_directory: bool
    migrated_target: Path | None


@dataclass(frozen=True)
class TransactionOutcome:
    result: TransactionResult
    message: str
    previous_target: str | None = None


def _live_path(live_dir: Path) -> Path:
    live = live_dir.expanduser()
    if not live.is_absolute():
        raise UnsafeDeploymentPathError("live directory must be an absolute path")
    live = live.absolute()
    if not live.name or live.name in {".", ".."}:
        raise UnsafeDeploymentPathError("invalid live directory")
    resolved = live.parent.resolve() / live.name
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise UnsafeDeploymentPathError("live directory cannot be a filesystem root or the user's home")
    return resolved


def _release_root(live_dir: Path) -> Path:
    root = release_root(_live_path(live_dir))
    if root.is_symlink():
        raise UnsafeDeploymentPathError("release root must not be a symlink")
    resolved = root.resolve()
    if resolved.parent != _live_path(live_dir).parent.resolve():
        raise UnsafeDeploymentPathError("release root must be a direct child of the live directory parent")
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def ensure_candidate_available(live_dir: Path, candidate: Path) -> Path:
    """Validate a fresh, non-symlink candidate without changing the filesystem."""
    live = _live_path(live_dir)
    releases = _release_root(live)
    target = candidate.expanduser()
    if not target.is_absolute():
        raise UnsafeDeploymentPathError("candidate must be an absolute path")
    target = target.absolute()
    resolved_parent = target.parent.resolve()
    if resolved_parent != releases or not _is_within(resolved_parent, live.parent.resolve()):
        raise UnsafeDeploymentPathError("candidate must be a direct child of the dashboard release root")
    target = resolved_parent / target.name
    if re.fullmatch(r"[0-9a-f]{40}", target.name) is None:
        raise UnsafeDeploymentPathError("candidate release directory must be a full lowercase commit SHA")
    if target.is_symlink():
        raise UnsafeDeploymentPathError("candidate path must not be a symlink")
    if target.exists():
        raise FileExistsError(f"candidate release already exists: {target}")
    if live.is_symlink():
        prior = live.resolve(strict=True)
        if not prior.is_dir() or not _is_within(prior, releases):
            raise UnsafeDeploymentPathError("current live symlink must resolve inside the release root")
    elif live.exists() and not live.is_dir():
        raise UnsafeDeploymentPathError("live path must be a directory or release symlink")
    elif live.is_dir():
        legacy = releases / "legacy-pre-auto-deploy"
        if legacy.exists() or legacy.is_symlink():
            raise FileExistsError(f"legacy migration target already exists: {legacy}")
    return target


@contextmanager
def deployment_lock(live_dir: Path) -> Iterator[None]:
    """Serialize local deploy transactions; a stale lock fails closed."""
    live = _live_path(live_dir)
    releases_path = live.parent / "dashboard-releases"
    if releases_path.is_symlink():
        raise UnsafeDeploymentPathError("release root must not be a symlink")
    releases = releases_path.resolve()
    if releases.parent != live.parent.resolve():
        raise UnsafeDeploymentPathError("release root must be inside the live directory parent")
    releases.mkdir(parents=True, exist_ok=True)
    lock = releases / ".node-auto-deploy.lock"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock, flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"another deployment owns {lock}; inspect and remove a stale lock manually") from exc
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(f"pid={os.getpid()}\n")
        yield
    finally:
        lock.unlink(missing_ok=True)


def _checked_existing_candidate(live_dir: Path, candidate: Path) -> tuple[Path, Path]:
    live = _live_path(live_dir)
    releases = _release_root(live)
    target = candidate.expanduser().absolute()
    if target.is_symlink() or target.parent.resolve() != releases or not target.is_dir():
        raise UnsafeDeploymentPathError("candidate must be a real directory inside the release root")
    return live, releases


def _reject_symlink_components(path: Path, stop: Path) -> None:
    current = path
    while current != stop and _is_within(current, stop):
        if current.is_symlink():
            raise UnsafeDeploymentPathError(f"refusing mutable-state symlink: {current}")
        current = current.parent
    if current != stop or stop.is_symlink():
        raise UnsafeDeploymentPathError(f"mutable path escapes trusted root: {path}")


def _copy_tree_contents(source: Path, target: Path, source_root: Path, target_root: Path) -> None:
    _reject_symlink_components(source, source_root)
    _reject_symlink_components(target, target_root)
    if not source.is_dir():
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        dest = target / item.name
        _reject_symlink_components(item, source_root)
        _reject_symlink_components(dest, target_root)
        if item.is_dir():
            _copy_tree_contents(item, dest, source_root, target_root)
        elif item.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
        else:
            raise UnsafeDeploymentPathError(f"unsupported mutable-state entry: {item}")


_MUTABLE_TREES = (Path("config"), Path("backend/data"))
_MUTABLE_FILES = (Path("backend/machine_registry.yml"), Path("agent_profiles.json"), Path("local_apps.json"))
_MUTABLE_SUFFIXES = (
    ".db",
    ".db-wal",
    ".db-shm",
    ".db-journal",
    ".sqlite",
    ".sqlite-wal",
    ".sqlite-shm",
    ".sqlite-journal",
    ".sqlite3",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".sqlite3-journal",
    ".ndjson",
    ".jsonl",
)
_IMMUTABLE_DIRECTORIES = {
    ".cache",
    ".git",
    ".venv",
    "__pycache__",
    "env",
    "node_modules",
    "uv-bootstrap",
    "venv",
    "wheels",
}


def _is_mutable_root_file(name: str) -> bool:
    return (
        name.startswith(".")
        and name.endswith("_state.json")
        or name.endswith("_history.json")
        or name.endswith(_MUTABLE_SUFFIXES)
    )


def _copy_runtime_artifacts(source: Path, target: Path, source_root: Path, target_root: Path) -> None:
    """Copy state-shaped files recursively while pruning application/runtime bundles."""
    _reject_symlink_components(source, source_root)
    for item in source.iterdir():
        if item.is_dir() and item.name in _IMMUTABLE_DIRECTORIES:
            continue
        dest = target / item.name
        _reject_symlink_components(item, source_root)
        _reject_symlink_components(dest, target_root)
        if item.is_dir():
            _copy_runtime_artifacts(item, dest, source_root, target_root)
        elif item.is_file() and _is_mutable_root_file(item.name):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)


def copy_mutable_state(source_dir: Path, candidate: Path) -> None:
    """Copy documented operator state into the candidate without following symlinks."""
    source = source_dir.resolve(strict=True)
    target = candidate.resolve(strict=True)
    if source == target:
        raise UnsafeDeploymentPathError("source and candidate must differ")
    for relative in _MUTABLE_TREES:
        _copy_tree_contents(source / relative, target / relative, source, target)
    for relative in _MUTABLE_FILES:
        src = source / relative
        dst = target / relative
        _reject_symlink_components(src, source)
        _reject_symlink_components(dst, target)
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    env_file = source / ".env"
    _reject_symlink_components(env_file, source)
    _reject_symlink_components(target / ".env", target)
    if env_file.is_file():
        shutil.copy2(env_file, target / ".env")
    _copy_runtime_artifacts(source, target, source, target)


def _atomic_pointer(live: Path, target: Path) -> None:
    tmp = live.with_name(f"{live.name}.switch.tmp")
    if tmp.exists() or tmp.is_symlink():
        raise FileExistsError(f"temporary live pointer already exists: {tmp}")
    tmp.symlink_to(target, target_is_directory=True)
    try:
        os.replace(tmp, live)
    finally:
        if tmp.is_symlink():
            tmp.unlink()


def activate_release(live_dir: Path, candidate: Path) -> SwitchReceipt:
    """Switch to a candidate and retain an exact, restorable prior target."""
    live, releases = _checked_existing_candidate(live_dir, candidate)
    previous: Path | None = None
    plain = False
    migrated: Path | None = None
    if live.is_symlink():
        previous = live.resolve(strict=True)
        if not previous.is_dir() or not _is_within(previous, releases):
            raise UnsafeDeploymentPathError("current live symlink must resolve inside the release root")
    elif live.is_dir():
        legacy = releases / "legacy-pre-auto-deploy"
        if legacy.exists() or legacy.is_symlink():
            raise FileExistsError(f"legacy migration target already exists: {legacy}")
        releases.mkdir(parents=True, exist_ok=True)
        os.replace(live, legacy)
        previous = legacy
        migrated = legacy
        plain = True
    elif live.exists():
        raise UnsafeDeploymentPathError("live path must be a directory or release symlink")
    try:
        _atomic_pointer(live, candidate.resolve())
    except Exception:
        if plain and migrated is not None and migrated.is_dir() and not live.exists():
            os.replace(migrated, live)
        raise
    return SwitchReceipt(previous, plain, migrated)


def restore_previous_target(live_dir: Path, receipt: SwitchReceipt) -> None:
    """Restore the prior symlink target or move the migrated plain tree back."""
    live = _live_path(live_dir)
    if receipt.previous_target is None:
        if live.is_symlink():
            live.unlink()
        elif live.exists():
            raise UnsafeDeploymentPathError("cannot remove a non-symlink live path during rollback")
        return
    previous = receipt.previous_target.resolve(strict=True)
    if not previous.is_dir():
        raise UnsafeDeploymentPathError("previous target is not a directory")
    if receipt.previous_was_plain_directory:
        if previous.parent.resolve() != _release_root(live):
            raise UnsafeDeploymentPathError("migrated legacy target escaped the release root")
        if live.is_symlink():
            live.unlink()
        elif live.exists():
            raise UnsafeDeploymentPathError("live path is occupied during rollback")
        os.replace(previous, live)
        return
    if not _is_within(previous, _release_root(live)):
        raise UnsafeDeploymentPathError("previous symlink target escaped the release root")
    _atomic_pointer(live, previous)


def run_deployment_transaction(
    live_dir: Path,
    candidate: Path,
    staged_candidate: Path,
    *,
    check_idle: Callable[[], bool],
    check_quiesced: Callable[[], bool],
    stop_service: Callable[[], None],
    restart_service: Callable[[], None],
    healthy: Callable[[], bool],
) -> TransactionOutcome:
    """Quiesce, carry state, switch, verify, and restore on every failure."""
    try:
        ensure_candidate_available(live_dir, candidate)
        _checked_existing_candidate(live_dir, staged_candidate)
    except Exception as exc:
        return TransactionOutcome("failed", f"candidate validation failed: {exc}")
    try:
        if not check_idle():
            return TransactionOutcome("skipped", "staff or chat activity remains in flight")
    except Exception as exc:
        return TransactionOutcome("failed", f"activity check unavailable: {exc}")

    stopped = False
    receipt: SwitchReceipt | None = None
    source = _live_path(live_dir)
    if source.is_symlink():
        source = source.resolve(strict=True)
    try:
        stopped = True
        stop_service()
        if not check_quiesced():
            raise RuntimeError("staff/chat activity remains after service stop")
        copy_mutable_state(source, staged_candidate)
        ensure_candidate_available(live_dir, candidate)
        os.rename(staged_candidate, candidate)
        receipt = activate_release(live_dir, candidate)
        restart_service()
        if not healthy():
            raise RuntimeError("health check failed after restart")
        previous_target = str(receipt.previous_target) if receipt.previous_target else None
        return TransactionOutcome(
            "success",
            "release switched and health verified",
            previous_target,
        )
    except Exception as exc:
        if receipt is not None:
            try:
                stop_service()
                restore_previous_target(live_dir, receipt)
                restart_service()
                if not healthy():
                    raise RuntimeError("previous release health check failed after rollback")
                previous_target = str(receipt.previous_target) if receipt.previous_target else None
                return TransactionOutcome(
                    "rollback",
                    f"{exc}; previous target restored and restarted",
                    previous_target,
                )
            except Exception as rollback_exc:
                previous_target = str(receipt.previous_target) if receipt.previous_target else None
                return TransactionOutcome(
                    "rollback_failed",
                    f"{exc}; rollback failed: {rollback_exc}",
                    previous_target,
                )
        if stopped:
            try:
                restart_service()
                if not healthy():
                    raise RuntimeError("previous release health check failed after resume")
            except Exception as resume_exc:
                return TransactionOutcome("rollback_failed", f"{exc}; old service restart failed: {resume_exc}")
        return TransactionOutcome("failed", str(exc))


def install_and_run_transaction(
    live_dir: Path,
    candidate: Path,
    install_candidate: Callable[[Path], None],
    *,
    check_idle: Callable[[], bool],
    check_quiesced: Callable[[], bool],
    stop_service: Callable[[], None],
    restart_service: Callable[[], None],
    healthy: Callable[[], bool],
) -> TransactionOutcome:
    """Install in a disposable stage before quiescing, then transact the switch."""
    try:
        ensure_candidate_available(live_dir, candidate)
        releases = _release_root(live_dir)
        releases.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{candidate.name[:12]}.staging-", dir=releases) as stage:
            staged_candidate = Path(stage)
            install_candidate(staged_candidate)
            return run_deployment_transaction(
                live_dir,
                candidate,
                staged_candidate,
                check_idle=check_idle,
                check_quiesced=check_quiesced,
                stop_service=stop_service,
                restart_service=restart_service,
                healthy=healthy,
            )
    except Exception as exc:
        return TransactionOutcome("failed", f"candidate install or validation failed: {exc}")
