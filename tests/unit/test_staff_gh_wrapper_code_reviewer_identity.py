"""Staff gh wrapper preserves Runner Dashboard tokens for code-reviewer (#4323)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_FAKE_GHS = "ghs_" + "C" * 40 + "_jwtbody"


def _load_gh_wrapper_module():
    from importlib.machinery import SourceFileLoader

    path = Path(__file__).resolve().parents[2] / "backend" / "staff" / "wrappers" / "gh"
    loader = SourceFileLoader("staff_wrappers_gh", str(path))
    spec = importlib.util.spec_from_loader("staff_wrappers_gh", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_code_reviewer_keeps_existing_ghs_token() -> None:
    wrapper = _load_gh_wrapper_module()
    env = {
        "STAFF_ROLE": "code-reviewer",
        "GH_TOKEN": _FAKE_GHS,
        "GITHUB_TOKEN": _FAKE_GHS,
    }
    out = wrapper._inject_fleet_github_token(env)
    assert out["GH_TOKEN"] == _FAKE_GHS
    assert out["GITHUB_TOKEN"] == _FAKE_GHS
