"""Structural tests for frontend polish scaffolding from issue #433."""

from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_MAIN = _REPO / "frontend" / "src" / "main.tsx"
_I18N = _REPO / "frontend" / "src" / "i18n" / "index.ts"
_EN = _REPO / "frontend" / "src" / "i18n" / "locales" / "en.json"
_DE = _REPO / "frontend" / "src" / "i18n" / "locales" / "de.json"
_SPEC = _REPO / "SPEC.md"


def test_i18n_scaffold_exports_locale_helpers() -> None:
    source = _I18N.read_text(encoding="utf-8")
    assert "export type LocaleCode" in source
    assert "export function normalizeLocale" in source
    assert "export function t" in source


def test_locale_catalogs_have_matching_message_keys() -> None:
    en = json.loads(_EN.read_text(encoding="utf-8"))
    de = json.loads(_DE.read_text(encoding="utf-8"))
    assert set(en) == set(de)
    assert "sw.updateReady.message" in en
    assert "sw.updateReady.title" in en


def test_service_worker_update_toast_is_registered() -> None:
    source = _MAIN.read_text(encoding="utf-8")
    assert "function registerServiceWorker" in source
    assert "controllerchange" in source
    assert "registration.addEventListener('updatefound'" in source
    assert "toastApi()?.showToast" in source
    assert "sw.updateReady.message" in source


def test_spec_documents_i18n_and_sw_update_toast() -> None:
    spec = _SPEC.read_text(encoding="utf-8", errors="ignore")
    normalized = " ".join(spec.lower().split())
    assert "frontend/src/i18n/locales" in spec
    assert "service worker update toast" in normalized
