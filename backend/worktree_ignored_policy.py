"""Which ignored paths may be lost when a git worktree is removed (#4540).

``git worktree remove`` deletes ignored files even when ``git status --porcelain`` is
empty. Unknown ignored paths block removal (fail closed); only listed regenerable
debris is treated as disposable.
"""

from __future__ import annotations

# Path prefixes (POSIX, relative to worktree root) safe to delete with the tree.
REGENERABLE_IGNORED_PATH_PREFIXES: tuple[str, ...] = (
    "__pycache__/",
    "node_modules/",
    ".venv/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    "dist/",
    "build/",
    "target/",
    ".staff/",
    ".gemini/",
    ".gaai/",
    ".tox/",
    ".eggs/",
    ".egg-info/",
    "htmlcov/",
    ".coverage",
    ".cache/",
)

IGNORED_PORCELAIN_PREFIX = "!! "
IGNORED_FILES_REASON = "ignored_files"


def _posix_rel(path: str) -> str:
    norm = path.replace("\\", "/").strip()
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.lstrip("/")


def is_regenerable_ignored_path(rel: str) -> bool:
    """Return whether an ignored relative path is known regenerable debris.

    Pre: ``rel`` is a repository-relative path using forward slashes.
    Post: ``True`` only when every path segment is under an allowlisted prefix pattern;
    unknown ignored paths return ``False`` (fail closed).
    """
    norm = _posix_rel(rel)
    if not norm or norm.startswith("/") or ".." in norm.split("/"):
        return False
    for prefix in REGENERABLE_IGNORED_PATH_PREFIXES:
        p = prefix.rstrip("/")
        if prefix.endswith("/"):
            if norm == p or norm.startswith(prefix):
                return True
        elif norm == p or norm.startswith(p + "/") or (p.startswith(".") and f"{p}/" in f"/{norm}/"):
            return True
    return False


def blocking_ignored_paths_from_porcelain(porcelain: str) -> list[str]:
    """Non-regenerable ignored paths from ``git status --porcelain --ignored`` output.

    Pre: ``porcelain`` is raw git stdout (may be empty).
    Post: sorted unique relative paths that must block worktree removal.
    """
    blocking: list[str] = []
    for raw in porcelain.splitlines():
        line = raw.rstrip("\n")
        if not line.startswith(IGNORED_PORCELAIN_PREFIX):
            continue
        rel = line[len(IGNORED_PORCELAIN_PREFIX) :].strip()
        if rel and not is_regenerable_ignored_path(rel):
            blocking.append(_posix_rel(rel))
    return sorted(set(blocking))


def ignored_files_removal_blocker(porcelain_ignored: str) -> str:
    """``ignored_files`` when any blocking ignored path is present, else ``""``."""
    return IGNORED_FILES_REASON if blocking_ignored_paths_from_porcelain(porcelain_ignored) else ""
