"""Open-source prep: the public sample overlay and its guard coverage (OSS-1, #3273)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import overlay_contract as oc  # noqa: E402
import publish_guard as pg  # noqa: E402

SAMPLE = _ROOT / "tests" / "fixtures" / "overlay" / "sample-public-overlay.json"
SAMPLE_REL = "tests/fixtures/overlay/sample-public-overlay.json"


def test_sample_overlay_validates() -> None:
    overlay = oc.load_overlay(SAMPLE)
    assert overlay.schema_version == oc.OVERLAY_SCHEMA_VERSION
    assert overlay.host_names()


def test_sample_overlay_uses_placeholder_values_only() -> None:
    overlay = oc.load_overlay(SAMPLE)
    for host in overlay.hosts:
        assert host.address.endswith(".example.invalid")
    for token_path in overlay.agent_token_paths.values():
        assert "example" in token_path.lower()


def test_sample_overlay_passes_publish_guard() -> None:
    rules = pg.load_rules()
    assert pg.check(_ROOT, [SAMPLE_REL], rules) == []


def test_flag_off_behaviour_unchanged_by_oss_prep() -> None:
    """OSS-1 adds docs/fixtures only; the overlay accessor's default (no-overlay) path is untouched."""
    assert oc.get_overlay({}) is None
    oc.assert_overlay_ready({})  # no-op
