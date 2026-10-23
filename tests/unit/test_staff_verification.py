"""Unit tests for target-PR fallback in staff post-run verification (#2478)."""

from __future__ import annotations

from datetime import UTC, datetime

from staff import verification as v
from staff.store import RunRecord

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=UTC)


class _TargetPrProbe:
    def __init__(self, target_pr: v.PullRequest | None) -> None:
        self.target_pr = target_pr
        self.find_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, int]] = []

    def find(self, repo: str, branch: str) -> v.PullRequest | None:
        self.find_calls.append((repo, branch))
        return None

    def get(self, repo: str, number: int) -> v.PullRequest | None:
        self.get_calls.append((repo, number))
        return self.target_pr

    def remote_ahead_by(self, repo: str, branch: str, base: str = "main") -> int:
        return 0


def test_pr_remediator_falls_back_to_target_pr_when_staff_branch_has_no_pr() -> None:
    rec = RunRecord(
        id="run-80777d147647",
        role="pr-remediator",
        provider="codex",
        model=None,
        machine="DeskComputer",
        repo="UpstreamDrift",
        target_kind="pr",
        target_ref="PR #13607",
        prompt="remediate",
        status="succeeded",
        branch="staff/pr-remediator-13607-dab0ab",
        ended_at="2026-10-23T13:55:00Z",
    )
    probe = _TargetPrProbe(v.PullRequest(number=13607, state="open", ci="green"))

    verdict = v.evaluate(rec, opens_pr=True, probe=probe, now=NOW)

    assert verdict.verification == "verified"
    assert verdict.pr_number == 13607
    assert probe.get_calls == [("UpstreamDrift", 13607)]


def test_default_fleet_barb_panel_and_turnover_and_token_scopes(tmp_path) -> None:
    from staff.barb_continuity import get_barb_continuity_store
    from staff.panel_models import PANEL_PRESETS, PanelCreateRequest
    from staff.tokens import mint_run_token, resolve_run_scopes

    assert PANEL_PRESETS[0]["id"] == "fleet-barb"
    req = PanelCreateRequest(topic="Architecture trade-off")
    assert [e.name for e in req.experts] == ["Astra", "Argon", "Fable", "Barb"]
    assert "staff.read" in resolve_run_scopes(["panel.consult"])
    assert "staff.chat" in resolve_run_scopes(["panel.consult"])
    tok = mint_run_token("issue-remediator", "run-test-panel", [], 60.0)
    assert tok.startswith("run_")
    store = get_barb_continuity_store(tmp_path / "barb.sqlite3")
    entry = store.append_turnover(scope="panel", author="Barb", persona="barb", summary="Panel th_1 completed")
    assert "Panel th_1 completed" in entry["summary"]
