"""Tests for single-root pyproject.toml + uv.lock consolidation — issue #333.

Verifies:
- Only one pyproject.toml exists at the repo root (no backend/pyproject.toml)
- Only one uv.lock exists at the repo root (no backend/uv.lock)
- Root pyproject.toml has [tool.uv] section
- deploy/setup.sh and deploy/update-deployed.sh reference uv sync --frozen
- backend/requirements.txt versions match root pyproject.toml deps
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Single pyproject.toml
# ---------------------------------------------------------------------------


def test_no_backend_pyproject_toml() -> None:
    """backend/pyproject.toml must not exist — root pyproject.toml is canonical."""
    backend_pyproject = REPO_ROOT / "backend" / "pyproject.toml"
    assert not backend_pyproject.exists(), (
        "backend/pyproject.toml must be deleted — use root pyproject.toml only (issue #333)"
    )


def test_root_pyproject_toml_exists() -> None:
    """Root pyproject.toml must exist."""
    assert (REPO_ROOT / "pyproject.toml").exists()


def test_root_pyproject_has_tool_uv_section() -> None:
    """Root pyproject.toml must have [tool.uv] section."""
    content = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.uv]" in content, "pyproject.toml must have [tool.uv] section (issue #333)"


# ---------------------------------------------------------------------------
# Single uv.lock
# ---------------------------------------------------------------------------


def test_no_backend_uv_lock() -> None:
    """backend/uv.lock must not exist — root uv.lock is canonical."""
    backend_lock = REPO_ROOT / "backend" / "uv.lock"
    assert not backend_lock.exists(), (
        "backend/uv.lock must be deleted — use root uv.lock only (issue #333)"
    )


def test_root_uv_lock_exists() -> None:
    """Root uv.lock must exist and be committed."""
    lock_path = REPO_ROOT / "uv.lock"
    assert lock_path.exists(), "uv.lock must exist at repo root (issue #333)"
    assert lock_path.stat().st_size > 0, "uv.lock must not be empty"


# ---------------------------------------------------------------------------
# Deploy scripts reference uv sync
# ---------------------------------------------------------------------------


def test_setup_sh_references_uv_sync() -> None:
    """deploy/setup.sh must use uv sync --frozen --no-dev (issue #333)."""
    content = (REPO_ROOT / "deploy" / "setup.sh").read_text(encoding="utf-8")
    assert "uv sync --frozen --no-dev" in content, (
        "deploy/setup.sh must use 'uv sync --frozen --no-dev' for reproducible installs"
    )


def test_update_deployed_sh_references_uv_sync() -> None:
    """deploy/update-deployed.sh must use uv sync --frozen --no-dev (issue #333)."""
    content = (REPO_ROOT / "deploy" / "update-deployed.sh").read_text(encoding="utf-8")
    assert "uv sync --frozen --no-dev" in content, (
        "deploy/update-deployed.sh must use 'uv sync --frozen --no-dev' for reproducible installs"
    )


# ---------------------------------------------------------------------------
# Version parity between root pyproject.toml and backend/requirements.txt
# ---------------------------------------------------------------------------


def _parse_requirements_txt(path: Path) -> dict[str, str]:
    """Parse a requirements.txt and return {package_lower: version}."""
    deps: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, version = line.split("==", 1)
            deps[name.strip().lower()] = version.strip()
    return deps


def _parse_pyproject_deps(path: Path) -> dict[str, str]:
    """Parse [project].dependencies from pyproject.toml and return {package_lower: version}."""
    try:
        import tomllib  # noqa: PLC0415
    except ImportError:
        import tomli as tomllib  # type: ignore[no-reattr,import-not-found]  # noqa: PLC0415

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deps: dict[str, str] = {}
    for dep in data.get("project", {}).get("dependencies", []):
        dep = dep.strip()
        if "==" in dep:
            # Handle extras like "uvicorn[standard]==0.46.0"
            name_part, version = dep.split("==", 1)
            name = name_part.split("[")[0].strip()
            deps[name.lower()] = version.strip()
    return deps


def test_backend_requirements_versions_match_pyproject() -> None:
    """backend/requirements.txt versions must match root pyproject.toml (issue #333).

    If backend/requirements.txt still exists as a deploy fallback, its pinned
    versions must not diverge from the canonical pyproject.toml.
    """
    req_path = REPO_ROOT / "backend" / "requirements.txt"
    if not req_path.exists():
        return  # Already deleted — pass

    pyproject_path = REPO_ROOT / "pyproject.toml"
    req_deps = _parse_requirements_txt(req_path)
    pyproject_deps = _parse_pyproject_deps(pyproject_path)

    mismatches: list[str] = []
    for pkg, req_ver in req_deps.items():
        if pkg in pyproject_deps and pyproject_deps[pkg] != req_ver:
            mismatches.append(f"{pkg}: requirements.txt={req_ver}, pyproject.toml={pyproject_deps[pkg]}")

    assert not mismatches, (
        "backend/requirements.txt versions diverge from root pyproject.toml:\n"
        + "\n".join(mismatches)
    )
