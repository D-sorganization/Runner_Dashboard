from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOBILE_DIR = ROOT / "tests" / "frontend" / "mobile"
VIEWPORTS = MOBILE_DIR / "viewport_profiles.json"
VIEWPORT_SCHEMA = MOBILE_DIR / "viewport_profiles.schema.json"
TOUCH_HELPERS = MOBILE_DIR / "touch_helpers.js"
INDEX_HTML = ROOT / "frontend" / "index.html"
LEGACY_SOURCE = ROOT / "frontend" / "src" / "legacy" / "App.tsx"


def _viewport_config() -> dict:
    return json.loads(VIEWPORTS.read_text(encoding="utf-8"))


def test_mobile_viewport_profiles_lock_required_issue_202_dimensions() -> None:
    config = _viewport_config()

    profiles = {profile["name"]: profile for profile in config["profiles"]}

    assert config["$schema"] == "./viewport_profiles.schema.json"
    assert VIEWPORT_SCHEMA.exists()
    assert sorted(profiles) == [
        "epic-compact-375",
        "epic-standard-412",
        "iphone-12",
        "pixel-5",
    ]
    assert profiles["iphone-12"]["viewport"] == {"width": 390, "height": 844}
    assert profiles["pixel-5"]["viewport"] == {"width": 393, "height": 851}
    assert profiles["epic-compact-375"]["viewport"] == {"width": 375, "height": 812}
    assert profiles["epic-standard-412"]["viewport"] == {"width": 412, "height": 915}
    assert config["playwright"] == {
        "browserName": "chromium",
        "headless": True,
        "hasTouch": True,
        "isMobile": True,
    }


def test_mobile_smoke_page_contract_targets_existing_frontend_markers() -> None:
    config = _viewport_config()
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = LEGACY_SOURCE.read_text(encoding="utf-8")
    queue_index = ROOT / "frontend" / "src" / "pages" / "Queue" / "index.tsx"
    if queue_index.exists():
        js += "\n" + queue_index.read_text(encoding="utf-8")

    smoke_pages = config["smokePages"]
    assert {page["name"] for page in smoke_pages} >= {
        "Fleet",
        "Queue",
        "Workflows",
        "Credentials",
        "Reports",
        "Assessments",
        "Feature Requests",
        "Maxwell",
    }
    for page in smoke_pages:
        assert page["tabLabel"], f"{page['name']} must name the tab a smoke test opens"
        assert page["goldenInteraction"], f"{page['name']} must describe one golden mobile action"
        assert page["requiredMarkers"], f"{page['name']} must include static frontend markers"
        for marker in page["requiredMarkers"]:
            assert marker in html or marker in js, (
                f"{page['name']} marker {marker!r} is missing from "
                "frontend/index.html or frontend/src/legacy/App.tsx"
            )


def test_touch_helper_scaffold_exports_readable_mobile_actions() -> None:
    helpers = TOUCH_HELPERS.read_text(encoding="utf-8")

    for profile_name in [
        "iphone-12",
        "pixel-5",
        "epic-compact-375",
        "epic-standard-412",
    ]:
        assert profile_name in helpers
    assert "export async function tap(page, target" in helpers
    assert "export async function swipe(page, start, end" in helpers
    assert "export async function longPress(page, target" in helpers
    assert "locator.tap" in helpers
    assert "boundingBox()" in helpers


def test_visual_regression_baselines_are_scaffolded_but_not_silently_enabled() -> None:
    config = _viewport_config()
    visual = config["visualRegression"]

    assert visual == {
        "state": "scaffolded",
        "enabledInCi": False,
        "baselineDir": "tests/frontend/mobile/__screenshots__",
        "updateFlag": "--update-snapshots",
        "failureArtifacts": ["diff", "actual", "expected"],
    }
    assert not (MOBILE_DIR / "__screenshots__").exists()
