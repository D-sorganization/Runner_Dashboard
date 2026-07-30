from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import dashboard_config  # noqa: E402


def _repo_version() -> str:
    version_text = (REPO_ROOT / "VERSION").read_text(encoding="utf-8")
    for raw_line in version_text.splitlines():
        candidate = raw_line.strip()
        if candidate and not candidate.startswith("#"):
            assert re.fullmatch(r"\d+\.\d+\.\d+", candidate)
            return candidate
    raise AssertionError("VERSION must contain a MAJOR.MINOR.PATCH release")


def test_static_release_metadata_tracks_version_file() -> None:
    version = _repo_version()

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_json = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    openapi_snapshot = json.loads((REPO_ROOT / "frontend" / "src" / "lib" / "openapi.json").read_text(encoding="utf-8"))
    spec_text = (REPO_ROOT / "SPEC.md").read_text(encoding="utf-8")
    changelog_text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert pyproject["project"]["version"] == version
    assert package_json["version"] == version
    assert package_lock["version"] == version
    assert package_lock["packages"][""]["version"] == version
    assert openapi_snapshot["info"]["version"] == version
    assert f"**Application Version:** {version} (see `VERSION`)" in spec_text
    assert f"## [{version}]" in changelog_text


def test_backend_runtime_version_tracks_version_file() -> None:
    version = _repo_version()

    assert dashboard_config.VERSION == version

    server_tree = ast.parse((REPO_ROOT / "backend" / "server.py").read_text(encoding="utf-8"))
    fastapi_calls = [
        node
        for node in ast.walk(server_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FastAPI"
    ]
    assert len(fastapi_calls) == 1
    version_keywords = [keyword for keyword in fastapi_calls[0].keywords if keyword.arg == "version"]
    assert len(version_keywords) == 1
    assert ast.unparse(version_keywords[0].value) == "dashboard_config.VERSION"
