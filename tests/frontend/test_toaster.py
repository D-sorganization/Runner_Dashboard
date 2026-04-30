"""Static tests for the global Toaster primitive (issue #421).

These tests parse the frontend source files directly so they can run in the
backend-only CI quality gate without requiring a Node runtime.

Acceptance criteria covered here:
    1. ``frontend/src/primitives/Toaster.tsx`` exists and contains both
       ``aria-live="polite"`` and ``aria-live="assertive"`` strings — proving
       the polite/assertive live regions are wired up for screen readers.
    2. ``useToast`` is exported from the Toaster module.
    3. ``<Toaster />`` is mounted in ``frontend/src/main.tsx`` so the toast
       API is available globally.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_TOASTER = _REPO / "frontend" / "src" / "primitives" / "Toaster.tsx"
_PRIMITIVES_INDEX = _REPO / "frontend" / "src" / "primitives" / "index.ts"
_MAIN_TSX = _REPO / "frontend" / "src" / "main.tsx"


def test_toaster_file_exists() -> None:
    assert _TOASTER.is_file(), (
        f"Expected Toaster primitive at {_TOASTER.relative_to(_REPO)}; "
        "issue #421 requires a global Toaster."
    )


def test_toaster_declares_polite_live_region() -> None:
    source = _TOASTER.read_text(encoding="utf-8")
    assert 'aria-live="polite"' in source, (
        "Toaster must render a polite live region for normal toasts "
        "(issue #421 acceptance criterion)."
    )


def test_toaster_declares_assertive_live_region() -> None:
    source = _TOASTER.read_text(encoding="utf-8")
    assert 'aria-live="assertive"' in source, (
        "Toaster must render an assertive live region for critical/error "
        "toasts (issue #421 acceptance criterion)."
    )


def test_toaster_uses_status_and_alert_roles() -> None:
    source = _TOASTER.read_text(encoding="utf-8")
    assert 'role="status"' in source, (
        "Polite toasts must render inside role='status' for assistive tech."
    )
    assert 'role="alert"' in source, (
        "Assertive/error toasts must render inside role='alert' for assistive tech."
    )


def test_toaster_exports_use_toast_hook() -> None:
    source = _TOASTER.read_text(encoding="utf-8")
    assert "export function useToast" in source, (
        "Toaster module must export a useToast() hook."
    )


def test_toaster_exports_toaster_component() -> None:
    source = _TOASTER.read_text(encoding="utf-8")
    assert "export function Toaster" in source, (
        "Toaster module must export the <Toaster /> component."
    )


def test_primitives_barrel_reexports_toaster() -> None:
    source = _PRIMITIVES_INDEX.read_text(encoding="utf-8")
    assert "Toaster" in source and "useToast" in source, (
        "primitives/index.ts must re-export Toaster and useToast for ergonomic imports."
    )


def test_main_tsx_imports_toaster() -> None:
    source = _MAIN_TSX.read_text(encoding="utf-8")
    assert "Toaster" in source, (
        "main.tsx must import the Toaster component."
    )


def test_main_tsx_mounts_toaster_at_app_root() -> None:
    source = _MAIN_TSX.read_text(encoding="utf-8")
    assert "<Toaster" in source, (
        "main.tsx must mount <Toaster /> at the React root so the live "
        "regions are globally available (issue #421)."
    )


def test_toaster_includes_all_severity_variants() -> None:
    source = _TOASTER.read_text(encoding="utf-8")
    for variant in ("info", "success", "warning", "error"):
        assert variant in source, (
            f"Toaster must support the '{variant}' severity variant."
        )


def test_toaster_handles_escape_key() -> None:
    source = _TOASTER.read_text(encoding="utf-8")
    assert "Escape" in source, (
        "Toaster must dismiss the topmost toast when Escape is pressed."
    )
