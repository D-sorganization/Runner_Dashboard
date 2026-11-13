"""Staff run → PR attribution for delivery metrics (#2117 gap 3)."""

from __future__ import annotations

from delivery_metrics.attribution import (
    PrAttribution,
    attach_attribution,
    build_attribution_index,
    dimension_groups,
)
from staff.store import RunRecord, RunStore


def _run(**kw: object) -> RunRecord:
    base = {
        "id": "r1",
        "role": "barb",
        "provider": "claude",
        "model": "sonnet",
        "machine": "node",
        "repo": "Runner_Dashboard",
        "target_kind": "issue",
        "target_ref": "1",
        "prompt": "ship",
        "pr_number": 2111,
    }
    base.update(kw)
    return RunRecord(**base)  # type: ignore[arg-type]


def test_build_attribution_index_prefers_latest_run() -> None:
    runs = [
        _run(id="old", created_at="2026-10-01T00:00:00Z", role="coder"),
        _run(id="new", created_at="2026-10-20T00:00:00Z", role="barb", provider="codex", model="gpt"),
    ]
    index = build_attribution_index(runs, org="D-sorganization")
    attr = index[("D-sorganization/Runner_Dashboard", 2111)]
    assert attr == PrAttribution(role="barb", agent="codex", model="gpt")


def test_dimension_groups_bucket_by_role_agent_model() -> None:
    rows = [
        attach_attribution(
            {"repo_full": "O/R", "number": 1, "ci_rounds_to_green": 2, "merged": True},
            PrAttribution("barb", "claude", "sonnet"),
        ),
        attach_attribution(
            {"repo_full": "O/R", "number": 2, "ci_rounds_to_green": 4, "merged": False},
            PrAttribution("barb", "claude", "sonnet"),
        ),
        attach_attribution(
            {"repo_full": "O/R", "number": 3, "ci_rounds_to_green": 1, "merged": True},
            None,
        ),
    ]
    groups = dimension_groups(rows)
    assert groups["by_role"][0]["key"] == "barb"
    assert groups["by_role"][0]["prs"] == 2
    assert groups["by_role"][-1]["key"] == "unattributed"
    assert groups["by_agent"][0]["key"] == "claude"
    assert "by_model" in groups


def test_list_runs_for_prs_includes_physical_retry_with_pr_number(tmp_path) -> None:
    store = RunStore(tmp_path / "runs.db")
    root = _run(id="root", pr_number=None, created_at="2026-10-01T00:00:00Z")
    store.create_run(root)
    retry = _run(
        id="retry",
        retry_of="root",
        pr_number=42,
        provider="codex",
        created_at="2026-10-02T00:00:00Z",
    )
    store.create_run(retry)
    runs = store.list_runs_for_prs([("Runner_Dashboard", 42)], logical_only=False)
    assert len(runs) == 1
    assert runs[0].id == "retry"
    index = build_attribution_index(runs)
    assert index[("D-sorganization/Runner_Dashboard", 42)].agent == "codex"
