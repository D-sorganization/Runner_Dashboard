"""CI guard: the documented Quick Start script is internally consistent (#945).

A full clean-checkout smoke job (provision venv + npm build + curl /) is run in
the deploy pipeline; these fast static checks fail in unit CI the moment the
script regresses to the broken pre-#945 behaviour (wrong requirements path,
silent fastapi+uvicorn fallback, or no frontend build).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "start-dashboard.sh"


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_exists() -> None:
    assert SCRIPT.is_file(), "start-dashboard.sh missing"


def test_requirements_points_at_repo_root_not_backend() -> None:
    text = _script_text()
    # The deps live in the repo-root requirements.txt; backend/requirements.txt
    # never existed (the #945 bug).
    assert "BACKEND_DIR}/requirements.txt" not in text
    assert 'REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"' in text
    assert (REPO_ROOT / "requirements.txt").is_file()
    assert not (REPO_ROOT / "backend" / "requirements.txt").exists()


def test_no_silent_fastapi_only_fallback() -> None:
    text = _script_text()
    # The old fallback `pip install fastapi 'uvicorn[standard]'` silently
    # produced a server that crashed on `import httpx/psutil/yaml`.
    assert "pip install fastapi" not in text
    # Missing requirements must fail loudly.
    assert "requirements file not found" in text


def test_script_builds_or_demands_frontend_bundle() -> None:
    text = _script_text()
    # The backend serves the SPA from frontend/dist/; the script must build it
    # (or fail with instructions) so Quick Start yields a working app.
    assert "dist" in text and "npm run build" in text


def test_no_backend_requirements_reference_in_docs() -> None:
    for doc in ("README.md", "CLAUDE.md"):
        text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        assert "backend/requirements.txt" not in text, f"{doc} still references the phantom backend/requirements.txt"
