"""The Playwright E2E job must also exercise the mobile viewport projects.

`tests/e2e/mobile.spec.ts` is written against the `chromium-iphone-12` and
`chromium-pixel-5` projects defined in `playwright.config.ts` (derived from
`tests/frontend/mobile/viewport_profiles.json`), but every test in it skips
unless the `isMobile` fixture is set. The `playwright-e2e` CI job has only
ever run `--project=chromium-desktop`, so the mobile suite has never
executed in CI (issue #3717).

Tracking: issue #3717.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_FRONTEND_TESTS = Path(__file__).parent.parent / ".github" / "workflows" / "frontend-tests.yml"


def _playwright_e2e_run_steps() -> list[dict]:
    workflow = yaml.safe_load(_FRONTEND_TESTS.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["playwright-e2e"]["steps"]
    return [s for s in steps if "run" in s]


def test_playwright_e2e_job_runs_mobile_viewport_projects() -> None:
    run_steps = _playwright_e2e_run_steps()
    mobile_steps = [s for s in run_steps if "chromium-iphone-12" in s["run"] and "chromium-pixel-5" in s["run"]]
    assert len(mobile_steps) == 1, "expected exactly one step running the mobile viewport projects"
    assert "tests/e2e/mobile.spec.ts" in mobile_steps[0]["run"], (
        "the mobile viewport run should be scoped to the mobile-only spec file, "
        "not the full suite, to keep the self-hosted job's runtime bounded"
    )


def test_playwright_e2e_desktop_step_still_present() -> None:
    run_steps = _playwright_e2e_run_steps()
    desktop_steps = [s for s in run_steps if "--project=chromium-desktop" in s["run"]]
    assert len(desktop_steps) == 1
