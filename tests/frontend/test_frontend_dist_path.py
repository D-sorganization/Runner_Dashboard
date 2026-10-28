from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_vite_server_launcher_and_readme_use_repo_root_dist() -> None:
    vite_config = (ROOT / "vite.config.ts").read_text(encoding="utf-8")
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    launcher = (ROOT / "start-dashboard.sh").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "outDir: '../dist'" in vite_config
    assert 'Path(__file__).parent.parent / "dist"' in server
    assert 'DIST_DIR="${SCRIPT_DIR}/dist"' in launcher
    assert "frontend/dist" not in launcher
    assert "repo-root" in readme
    assert "`dist/` via `npm ci && npm run build`" in readme
    assert "`frontend/dist/`" not in readme


def test_docker_builds_frontend_and_copies_root_dist_into_runtime_image() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "AS frontend-builder" in dockerfile
    assert "RUN npm ci" in dockerfile
    assert "RUN npm run build" in dockerfile
    assert "COPY --from=frontend-builder --chown=appuser:appuser /app/dist/ ./dist/" in dockerfile
    assert "--create-home" in dockerfile


def test_backend_path_is_ready_before_sibling_imports() -> None:
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")

    assert server.index("sys.path.insert(0, str(BACKEND_DIR))") < server.index(
        "from browser_ingress_guard import"
    )
