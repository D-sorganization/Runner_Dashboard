"""Dispatch bundle: standards + skill matching (#2157 slice 2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from staff.dispatch_bundle import DispatchContractError, resolve_dispatch_bundle  # noqa: E402
from staff.skills.registry import clear_skills_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _skills_env(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_skills_cache()
    root = Path(__file__).resolve().parents[1] / "fixtures" / "skills_dispatch"
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(root))
    yield
    clear_skills_cache()


def test_resolve_injects_versions() -> None:
    bundle = resolve_dispatch_bundle(role="night-watch", repo="AffineDrift")
    assert bundle.standards_version == "1.0.0"
    assert bundle.skill_pin.startswith("fleet-staff-unattended@1.0.0+")
    assert "TDD" in bundle.standards_block
    assert "unattended" in bundle.skill_block.lower()


def test_rejects_when_no_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_SKILLS_DIR", "/nonexistent/skills")
    clear_skills_cache()
    with pytest.raises(DispatchContractError, match="no skill matches"):
        resolve_dispatch_bundle(role="unknown-role", repo="AffineDrift")
