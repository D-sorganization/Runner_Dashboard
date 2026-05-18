"""Tests for the dashboard's static-asset routes.

Background: `update-deployed.sh` now runs `vite build` and syncs `dist/` to
the deployed install (PR #669). But the FastAPI server only mounted
`/assets` — every other static URL referenced from `index.html`
(`/icons/icon-180.png`, `/favicon.ico`, `/manifest.webmanifest`, etc.) fell
through to the SPA catch-all and returned `index.html` as `text/html`.

Symptom: Windows taskbar pinned shortcuts had no favicon because the
browser fetched `/favicon.ico` and got 1.8 KB of HTML instead of an icon.

This module pins the contract that each of these URLs serves real binary
content with the right Content-Type when the matching file exists in
`dist/`.
"""

from __future__ import annotations

from pathlib import Path

# These tests are pure structural greps over backend/server.py — they
# never import server, so they're safe on every platform (including
# Windows-mounted filesystems where security.py's POSIX-mode check would
# otherwise trip).
_SERVER_SRC = (Path(__file__).resolve().parent.parent / "backend" / "server.py").read_text(
    encoding="utf-8"
)


def test_server_mounts_icons_dir() -> None:
    """/icons/<name>.png must be mounted as StaticFiles so PNGs are served
    with the correct Content-Type instead of being shadowed by the SPA
    catch-all."""
    src = _SERVER_SRC
    assert '"/icons"' in src
    assert "StaticFiles(directory=str(_icons_dir))" in src


def test_server_has_favicon_route() -> None:
    """Windows pinned-shortcut creation probes /favicon.ico. Must not 404
    or return HTML."""
    src = _SERVER_SRC
    assert '@app.get("/favicon.ico")' in src


def test_server_has_service_worker_route() -> None:
    """PWA install requires /sw.js at the origin root."""
    src = _SERVER_SRC
    assert '@app.get("/sw.js")' in src


def test_server_has_offline_route() -> None:
    src = _SERVER_SRC
    assert '@app.get("/offline.html")' in src


def test_server_has_robots_route() -> None:
    src = _SERVER_SRC
    assert '@app.get("/robots.txt")' in src
