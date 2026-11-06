"""Unit tests for codemap refresh (#3788 follow-up)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.codemap_refresh import build_report, refresh, render_repo_map


def test_render_repo_map_lists_entrypoints(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    assert "backend/server.py" in render_repo_map(repo)


def test_refresh_writes_freshness_report(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    payload = refresh(repo)
    report = json.loads((repo / "docs/codemap/freshness-report.json").read_text(encoding="utf-8"))
    assert report["commit_sha"] == payload["commit_sha"]
    assert (repo / "docs/codemap/repo-map.md").is_file()


def test_architecture_marked_stale_after_backend_change(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    refresh(repo)
    (repo / "backend" / "server.py").write_text("# changed\n", encoding="utf-8")
    _git(repo, "add", "backend/server.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=t@example.invalid", "commit", "-qm", "drift")
    assert "architecture" in build_report(repo)["stale_sections"]


def _seed_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs/architecture").mkdir(parents=True)
    (root / "docs/architecture/C4.md").write_text("# Architecture\n", encoding="utf-8")
    (root / "docs/codemap").mkdir(parents=True)
    (root / "docs/codemap/README.md").write_text("# Codemap\n", encoding="utf-8")
    (root / "docs/codemap.md").write_text("# Index\n", encoding="utf-8")
    (root / "backend/routers").mkdir(parents=True)
    (root / "backend/server.py").write_text("# server\n", encoding="utf-8")
    (root / "backend/identity.py").write_text("# id\n", encoding="utf-8")
    (root / "backend/routers/health.py").write_text("# health\n", encoding="utf-8")
    (root / "frontend/src").mkdir(parents=True)
    (root / "frontend/src/main.tsx").write_text("export {}\n", encoding="utf-8")
    (root / "frontend/index.html").write_text("<html></html>\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "clients/fleet").mkdir(parents=True)
    (root / "clients/fleet/fleetctl.py").write_text("# cli\n", encoding="utf-8")
    (root / "start-dashboard.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "stop-dashboard.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=t@example.invalid", "commit", "-qm", "seed")
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.check_call(["git", "-C", str(root), *args])
