"""Tests for Vite configuration and prevention of shadowing artifacts (#1549).

Ensures vite.config.ts is the sole configuration file, no compiled
vite.config.js or vite.config.d.ts is tracked or present to shadow it,
and that the dev proxy honours VITE_BACKEND_URL.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_VITE_CONFIG_TS = _REPO / "vite.config.ts"
_VITE_CONFIG_JS = _REPO / "vite.config.js"
_VITE_CONFIG_D_TS = _REPO / "vite.config.d.ts"
_GITIGNORE = _REPO / ".gitignore"
_STAFF_PLAYWRIGHT_CONFIG = _REPO / "tests" / "e2e" / "staff" / "playwright.config.ts"


def test_stale_vite_config_artifacts_do_not_exist() -> None:
    """Stale compiled vite config artifacts must not exist on disk."""
    assert _VITE_CONFIG_TS.is_file(), "vite.config.ts must exist as canonical Vite config"
    assert not _VITE_CONFIG_JS.exists(), (
        "vite.config.js must be removed; it shadows vite.config.ts and hardcodes /api proxy"
    )
    assert not _VITE_CONFIG_D_TS.exists(), "vite.config.d.ts must be removed as a stale compile artifact"


def test_no_stale_vite_config_is_tracked() -> None:
    """Git must not track vite.config.js or vite.config.d.ts."""
    result = subprocess.run(
        ["git", "ls-files", "vite.config.js", "vite.config.d.ts"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = result.stdout.strip().splitlines()
    assert not tracked, f"Stale artifacts tracked in git: {tracked}"


def test_gitignore_ignores_stale_vite_config_artifacts() -> None:
    """.gitignore must ignore vite.config.js and vite.config.d.ts if regenerated."""
    content = _GITIGNORE.read_text(encoding="utf-8")
    assert "vite.config.js" in content, ".gitignore must ignore vite.config.js"
    assert "vite.config.d.ts" in content, ".gitignore must ignore vite.config.d.ts"


def test_vite_config_ts_honours_backend_url() -> None:
    """vite.config.ts dev proxy must honour VITE_BACKEND_URL."""
    content = _VITE_CONFIG_TS.read_text(encoding="utf-8")
    assert "process.env.VITE_BACKEND_URL" in content
    assert "'/api': backendProxyTarget" in content or '"/api": backendProxyTarget' in content


def test_staff_playwright_config_does_not_workaround_vite_config() -> None:
    """Staff playwright config must not need the --config vite.config.ts workaround."""
    content = _STAFF_PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    assert "--config vite.config.ts" not in content, (
        "Workaround '--config vite.config.ts' should be dropped once vite.config.js is removed"
    )
