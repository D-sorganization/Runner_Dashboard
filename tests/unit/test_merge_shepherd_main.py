"""CLI entry tests for merge shepherd hold gate (#4248)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import merge_shepherd_pass as msp  # noqa: E402


def test_main_exits_nonzero_and_skips_graphql_when_hub_holds_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MERGE_SHEPHERD_HUB_HOLDS", "required")
    monkeypatch.delenv("RUNNER_DASHBOARD_URL", raising=False)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    def _should_not_run(*_a: object, **_k: object) -> None:
        raise AssertionError("graphql must not run when holds are unavailable")

    monkeypatch.setattr(msp, "_graphql", _should_not_run)
    code = msp.main(["--repo-root", str(_REPO_ROOT), "--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["armed"] == []
    assert payload["status"] == "holds_unavailable"
    assert str(payload["holds_unavailable"]).startswith("holds_unavailable:")
