"""Tests asserting exactly one canonical manifest.webmanifest and schema compliance.

AC #7 from issue #419: there must be exactly one manifest in the build output
and it must match the required schema (icons, shortcuts, share_target, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"
_PUBLIC_MANIFEST = _FRONTEND / "public" / "manifest.webmanifest"
_ROOT_MANIFEST = _FRONTEND / "manifest.webmanifest"
_ICONS_DIR = _FRONTEND / "public" / "icons"
_SCREENSHOTS_DIR = _FRONTEND / "public" / "screenshots"


# ---------------------------------------------------------------------------
# Exactly one manifest
# ---------------------------------------------------------------------------


def test_only_canonical_manifest_exists() -> None:
    """frontend/manifest.webmanifest must be deleted; only public/ copy survives."""
    assert _PUBLIC_MANIFEST.exists(), "frontend/public/manifest.webmanifest must exist"
    assert not _ROOT_MANIFEST.exists(), (
        "frontend/manifest.webmanifest must be deleted — "
        "frontend/public/manifest.webmanifest is the single source of truth"
    )


def test_manifest_is_valid_json() -> None:
    manifest = json.loads(_PUBLIC_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)


# ---------------------------------------------------------------------------
# Required top-level fields
# ---------------------------------------------------------------------------


def _manifest() -> dict:
    return json.loads(_PUBLIC_MANIFEST.read_text(encoding="utf-8"))


def test_manifest_has_required_fields() -> None:
    m = _manifest()
    for field in [
        "name",
        "short_name",
        "description",
        "start_url",
        "scope",
        "display",
        "background_color",
        "theme_color",
        "icons",
    ]:
        assert field in m, f"manifest missing required field: {field}"


def test_manifest_display_override_contains_window_controls_overlay() -> None:
    m = _manifest()
    assert "display_override" in m, "manifest must have display_override"
    assert "window-controls-overlay" in m["display_override"]
    assert "standalone" in m["display_override"]


# ---------------------------------------------------------------------------
# PNG icons at required sizes (192, 256, 384, 512) in any and maskable
# ---------------------------------------------------------------------------


def test_manifest_has_png_icons_at_required_sizes() -> None:
    m = _manifest()
    icons = m.get("icons", [])
    png_icons = [ic for ic in icons if ic.get("type") == "image/png"]
    sizes_present = {ic["sizes"] for ic in png_icons}
    for required_size in ["192x192", "256x256", "384x384", "512x512"]:
        assert required_size in sizes_present, f"manifest missing PNG icon at {required_size}"


def test_manifest_has_png_icons_with_any_purpose() -> None:
    m = _manifest()
    any_pngs = [ic for ic in m.get("icons", []) if ic.get("type") == "image/png" and "any" in ic.get("purpose", "")]
    assert len(any_pngs) >= 2, "manifest must have at least two PNG icons with purpose=any"


def test_manifest_has_png_icons_with_maskable_purpose() -> None:
    m = _manifest()
    maskable_pngs = [
        ic for ic in m.get("icons", []) if ic.get("type") == "image/png" and "maskable" in ic.get("purpose", "")
    ]
    assert len(maskable_pngs) >= 1, "manifest must have at least one PNG icon with purpose=maskable"


def test_png_icon_files_exist_on_disk() -> None:
    for size in [192, 256, 384, 512]:
        path = _ICONS_DIR / f"icon-{size}.png"
        assert path.exists(), f"PNG icon file missing: {path.relative_to(_REPO)}"
        # Validate PNG signature
        sig = path.read_bytes()[:8]
        assert sig == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a valid PNG"


def test_apple_touch_icon_png_exists() -> None:
    """icon-180.png must exist for the <link rel=apple-touch-icon> in index.html."""
    path = _ICONS_DIR / "icon-180.png"
    assert path.exists(), "icons/icon-180.png required for iOS apple-touch-icon"
    sig = path.read_bytes()[:8]
    assert sig == b"\x89PNG\r\n\x1a\n", "icon-180.png is not a valid PNG"


# ---------------------------------------------------------------------------
# apple-touch-icon in index.html points to PNG
# ---------------------------------------------------------------------------


def test_index_html_apple_touch_icon_references_png() -> None:
    html = (_FRONTEND / "index.html").read_text(encoding="utf-8")
    assert "apple-touch-icon" in html, "index.html must have apple-touch-icon link"
    assert "icon-180.png" in html, "apple-touch-icon must point to icons/icon-180.png (not icon.svg)"
    assert (
        'href="/icon.svg"' not in html
        or "apple-touch-icon" not in html.split('href="/icon.svg"')[0].rsplit("\n", 1)[-1]
    ), "apple-touch-icon must not reference icon.svg"


# ---------------------------------------------------------------------------
# Real screenshots (not SVG reused)
# ---------------------------------------------------------------------------


def test_manifest_has_real_screenshot_wide() -> None:
    m = _manifest()
    screenshots = m.get("screenshots", [])
    wide = [s for s in screenshots if s.get("form_factor") == "wide"]
    assert len(wide) >= 1, "manifest must have at least one wide screenshot"
    assert all(s.get("type") == "image/png" for s in wide), "wide screenshots must be PNG, not SVG"
    assert all("1280" in s.get("sizes", "") for s in wide), "wide screenshot must be 1280px wide"


def test_manifest_has_real_screenshot_mobile() -> None:
    m = _manifest()
    screenshots = m.get("screenshots", [])
    narrow = [s for s in screenshots if s.get("form_factor") == "narrow"]
    assert len(narrow) >= 1, "manifest must have at least one narrow/mobile screenshot"
    assert all(s.get("type") == "image/png" for s in narrow)


def test_screenshot_files_exist_on_disk() -> None:
    for fname in ["wide-1280x800.png", "mobile-375x812.png"]:
        path = _SCREENSHOTS_DIR / fname
        assert path.exists(), f"Screenshot file missing: {path.relative_to(_REPO)}"
        sig = path.read_bytes()[:8]
        assert sig == b"\x89PNG\r\n\x1a\n", f"{fname} is not a valid PNG"


# ---------------------------------------------------------------------------
# Shortcuts
# ---------------------------------------------------------------------------


def test_manifest_has_shortcuts() -> None:
    m = _manifest()
    assert "shortcuts" in m, "manifest must have shortcuts"
    shortcuts = m["shortcuts"]
    assert len(shortcuts) >= 3, "manifest must have at least 3 shortcuts"


def test_manifest_shortcuts_have_required_urls() -> None:
    m = _manifest()
    urls = {s.get("url") for s in m.get("shortcuts", [])}
    assert "/dispatch" in urls, "shortcuts must include /dispatch (Quick Dispatch)"
    assert "/queue" in urls, "shortcuts must include /queue (Queue Health)"
    assert "/maxwell" in urls, "shortcuts must include /maxwell (Maxwell Chat)"


# ---------------------------------------------------------------------------
# share_target
# ---------------------------------------------------------------------------


def test_manifest_has_share_target() -> None:
    m = _manifest()
    assert "share_target" in m, "manifest must have share_target"
    st = m["share_target"]
    assert "action" in st, "share_target must have action"
    assert "params" in st, "share_target must have params"
    params = st["params"]
    assert "url" in params, "share_target.params must include 'url'"
