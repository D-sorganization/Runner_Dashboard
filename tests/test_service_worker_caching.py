"""Source-level service worker cache contract tests."""

from __future__ import annotations

import re
from pathlib import Path

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
_SW = _FRONTEND_DIR / "public" / "sw.js"
_MAIN = _FRONTEND_DIR / "src" / "main.tsx"


def _read_sw() -> str:
    return _SW.read_text(encoding="utf-8")


def test_api_paths_are_network_only_and_no_store() -> None:
    content = _read_sw()

    assert "pathname.startsWith('/api/')" in content
    assert "networkOnly(request)" in content
    assert "new Request(request, { cache: 'no-store' })" in content
    assert ".catch(() => caches.match(request))" not in content


def test_api_contract_covers_sensitive_dashboard_prefixes() -> None:
    content = _read_sw()
    matcher = re.search(r"pathname\.startsWith\('/api/'\)", content)
    assert matcher, "service worker must match every /api/* path"

    protected_paths = [
        "/api/runs",
        "/api/queue",
        "/api/agents/worker-1",
        "/api/v1/staff/threads",
        "/api/fleet/orchestration",
    ]
    for path in protected_paths:
        assert path.startswith("/api/")


def test_cache_name_uses_build_id_from_registration_url() -> None:
    content = _read_sw()
    main_tsx = _MAIN.read_text(encoding="utf-8")

    assert "const BUILD_ID = new URL(self.location.href).searchParams.get('build') || 'dev';" in content
    assert "const CACHE_NAME = `runner-dashboard-${BUILD_ID}`;" in content
    assert "VITE_BUILD_ID" in main_tsx
    assert "register(`/sw.js?build=${encodeURIComponent(buildId)}`)" in main_tsx


def test_static_assets_are_cached_but_api_and_navigation_are_not() -> None:
    content = _read_sw()

    assert "const STATIC_EXTS = /\\.(?:js|css|svg|html|woff2|png|webp|ico)$/;" in content
    assert "isStaticAsset(url.pathname)" in content
    assert "event.respondWith(cacheFirst(request));" in content
    assert "request.mode === 'navigate'" in content
    assert "caches.match(OFFLINE_URL)" in content


def test_main_shows_reload_toast_on_controllerchange() -> None:
    main_tsx = _MAIN.read_text(encoding="utf-8")

    assert "navigator.serviceWorker.addEventListener('controllerchange'" in main_tsx
    assert "title: 'New version'" in main_tsx
    assert "actionLabel: 'Reload'" in main_tsx
    assert "onAction: () => window.location.reload()" in main_tsx
