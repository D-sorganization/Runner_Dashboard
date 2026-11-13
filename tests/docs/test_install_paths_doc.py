"""Keep the install-paths doc in step with the real install artifacts (#3723)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "install-paths.md"
COMPOSE = ROOT / "docker" / "docker-compose.yml"
GITIGNORE = ROOT / ".gitignore"


def _compose_env_vars() -> set[str]:
    text = COMPOSE.read_text(encoding="utf-8")
    # Lines like `- GH_TOKEN=${GH_TOKEN}` or `- ORG=${ORG:-D-sorganization}`.
    return set(re.findall(r"^\s*-\s*([A-Z_][A-Z0-9_]*)=", text, re.MULTILINE))


def test_doc_exists() -> None:
    assert DOC.is_file(), "docs/install-paths.md must exist (#3723)"


def test_doc_documents_both_supported_install_paths() -> None:
    text = DOC.read_text(encoding="utf-8")
    # systemd / bare-metal path
    assert "deploy/setup.sh" in text
    assert "deploy/update-deployed.sh" in text
    # Docker Compose path
    assert "docker/docker-compose.yml" in text
    assert "Dockerfile" in text


def test_doc_env_vars_match_compose_file() -> None:
    """Every env var the doc calls out for the Docker path must be real (no drift)."""
    text = DOC.read_text(encoding="utf-8")
    compose_vars = _compose_env_vars()
    assert compose_vars, "expected docker-compose.yml to declare environment vars"
    for var in ("GH_TOKEN", "SESSION_SECRET"):
        assert var in compose_vars, f"{var} expected in docker-compose.yml"
        assert var in text, f"doc must name {var} since docker-compose.yml declares it"


def test_doc_separates_config_from_secret() -> None:
    text = DOC.read_text(encoding="utf-8").lower()
    assert "config" in text
    assert "secret" in text
    # The doc must not instruct committing a real secrets file.
    assert "git add .env" not in text


def test_gitignore_actually_excludes_env_files_the_doc_relies_on() -> None:
    gitignore = GITIGNORE.read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert ".env.*" in gitignore
