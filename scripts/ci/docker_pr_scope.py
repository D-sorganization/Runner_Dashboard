"""Decide whether a pull request needs Docker image build/scan from changed paths."""

from __future__ import annotations

DOCKER_SCOPE_PREFIXES = (
    "backend/",
    "frontend/",
    "config/",
    "docker/",
)

DOCKER_SCOPE_EXACT = frozenset(
    {
        "Dockerfile",
        "requirements.lock.txt",
        "requirements.txt",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/docker-build.yml",
    }
)


def run_docker_for_paths(files: list[str]) -> bool:
    """Return True when Docker validation must run for these PR paths.

    Fails safe (returns True) when ``files`` is empty (e.g. git diff error or
    unfetched base ref) or truncated (>= 100 files).
    """
    if not files or len(files) >= 100:
        return True
    return any(
        path.startswith(DOCKER_SCOPE_PREFIXES)
        or path in DOCKER_SCOPE_EXACT
        or (path.startswith("Dockerfile") and path != "Dockerfile.sample")
        for path in files
    )


def main() -> None:
    import sys

    files = [line.strip() for line in sys.stdin if line.strip()]
    scope_failed = not files
    print(f"run_docker={'true' if run_docker_for_paths(files) else 'false'}")
    print(f"scope_failed={'true' if scope_failed else 'false'}")
    print(f"changed_file_count={len(files)}", file=sys.stderr)



if __name__ == "__main__":
    main()
