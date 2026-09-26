"""Tests for Vite configuration and dev proxy resolution (issue #1549).

Acceptance criteria:
- Remove the stale generated vite.config.js / vite.config.d.ts (confirm nothing imports them),
  and ignore them if a build step regenerates them.
- A test asserts that the dev proxy honours VITE_BACKEND_URL (or that no vite.config.js is tracked).
- Drop the --config vite.config.ts workaround from tests/e2e/staff/playwright.config.ts.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VITE_CONFIG_TS = _REPO_ROOT / "vite.config.ts"
_VITE_CONFIG_JS = _REPO_ROOT / "vite.config.js"
_VITE_CONFIG_DTS = _REPO_ROOT / "vite.config.d.ts"
_GITIGNORE = _REPO_ROOT / ".gitignore"
_STAFF_PLAYWRIGHT_CONFIG = _REPO_ROOT / "tests" / "e2e" / "staff" / "playwright.config.ts"


def _get_tracked_files(*filenames: str) -> list[str]:
    """Return tracked git files, handling Windows worktrees when running in WSL."""
    git_bin = shutil.which("git")
    if not git_bin:
        pytest.skip("git not available in environment")
    cmd = [git_bin, "-c", "safe.directory=*"]
    git_file = _REPO_ROOT / ".git"
    if git_file.is_file():
        content = git_file.read_text(encoding="utf-8").strip()
        if content.startswith("gitdir:"):
            raw_gitdir = content[len("gitdir:") :].strip()
            if os.name != "nt" and re.match(r"^[a-zA-Z]:", raw_gitdir):
                drive = raw_gitdir[0].lower()
                suffix = raw_gitdir[2:].replace("\\", "/")
                gitdir = f"/mnt/{drive}{suffix}"
            else:
                gitdir = raw_gitdir
            cmd.extend([f"--git-dir={gitdir}", f"--work-tree={_REPO_ROOT}"])
    cmd.extend(["ls-files", *filenames])
    res = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True, check=True)
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def test_stale_vite_config_artifacts_not_present() -> None:
    """vite.config.ts must be the sole config; stale .js/.d.ts must not exist."""
    assert _VITE_CONFIG_TS.is_file(), "vite.config.ts must exist at repo root"
    assert not _VITE_CONFIG_JS.exists(), (
        "stale vite.config.js must be removed: Vite resolves .js before .ts and shadows vite.config.ts (#1549)"
    )
    assert not _VITE_CONFIG_DTS.exists(), "generated vite.config.d.ts must not be committed to repo root (#1549)"


def test_stale_vite_config_artifacts_not_tracked_in_git() -> None:
    """git must not track vite.config.js or vite.config.d.ts."""
    tracked = _get_tracked_files("vite.config.js", "vite.config.d.ts")
    assert not tracked, f"Stale vite config files tracked in git: {tracked}"


def test_gitignore_ignores_vite_config_build_artifacts() -> None:
    """.gitignore must ignore regenerated vite.config.js and vite.config.d.ts."""
    gitignore_text = _GITIGNORE.read_text(encoding="utf-8")
    assert "vite.config.js" in gitignore_text, ".gitignore must ignore vite.config.js"
    assert "vite.config.d.ts" in gitignore_text, ".gitignore must ignore vite.config.d.ts"


def test_vite_config_ts_defines_proxy_from_backend_url() -> None:
    """vite.config.ts source contract must reference VITE_BACKEND_URL and DASHBOARD_PORT."""
    content = _VITE_CONFIG_TS.read_text(encoding="utf-8")
    assert "process.env.VITE_BACKEND_URL" in content
    assert "process.env.DASHBOARD_PORT" in content
    assert "backendProxyTarget" in content
    assert "'/api': backendProxyTarget" in content


def test_dev_proxy_honours_vite_backend_url() -> None:
    """Vite dev server default config resolution must honour VITE_BACKEND_URL (#1549)."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not available in environment")

    # Evaluate Vite's default config resolution without passing --config.
    test_target = "http://127.0.0.1:9876"
    script = (
        "import('vite').then(async (m) => {\n"
        "  const config = await m.resolveConfig({}, 'serve');\n"
        "  const proxy = config.server?.proxy?.['/api'];\n"
        "  const target = typeof proxy === 'string' ? proxy : proxy?.target;\n"
        "  console.log(JSON.stringify({ configFile: config.configFile, target }));\n"
        "}).catch((err) => {\n"
        "  console.error(err);\n"
        "  process.exit(1);\n"
        "});\n"
    )
    env = {**os.environ, "VITE_BACKEND_URL": test_target}
    res = subprocess.run(
        [node_bin, "--input-type=module", "-e", script],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    if res.returncode != 0:
        if "rollup" in res.stderr:
            pytest.skip(f"node lacks platform-native rollup binary in this environment: {res.stderr}")
        pytest.fail(f"Vite resolveConfig failed: {res.stderr}\nstdout: {res.stdout}")

    assert f'"target":"{test_target}"' in res.stdout.replace(" ", ""), (
        f"Expected dev proxy target to be {test_target}, got output:\n{res.stdout}"
    )


def test_staff_playwright_config_drops_vite_config_workaround() -> None:
    """tests/e2e/staff/playwright.config.ts must not pass redundant --config vite.config.ts."""
    content = _STAFF_PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    assert "--config vite.config.ts" not in content, (
        "workaround '--config vite.config.ts' should be dropped from staff playwright.config.ts (#1549)"
    )
