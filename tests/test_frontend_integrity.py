"""Static source integrity checks for frontend/index.html.

These tests do not execute JavaScript — they assert that the compiled single-file
frontend contains the structural markers expected by the dashboard design contract.
"""

from __future__ import annotations  # noqa: E402

import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
_INDEX_HTML = _FRONTEND_DIR / "index.html"


def _read_index() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _index_lines() -> list[str]:
    return _read_index().splitlines()


# ---------------------------------------------------------------------------
# Required tab/component function markers
# ---------------------------------------------------------------------------

_REQUIRED_FUNCTIONS = [
    "function FleetTab",
    "function HistoryTab",
    "function QueueTab",
    "function MachinesTab",
    "function OrgTab",
    "function TestsTab",
    "function StatsTab",
    "function ReportsTab",
    "function ScheduledJobsTab",
    "function LocalAppsTab",
    "function RemediationTab",
    "function WorkflowsTab",
    "function CredentialsTab",
    "function AssessmentsTab",
    "function FeatureRequestsTab",
    "function MaxwellTab",
    "function FleetOrchestrationTab",
    "function DashboardHelp",
]

# RunnerPlanTab is not yet implemented in the frontend; tracked for a future PR.
_XFAIL_FUNCTIONS = [
    "function RunnerPlanTab",
]


@pytest.mark.parametrize("marker", _REQUIRED_FUNCTIONS)
def test_required_function_present(marker: str) -> None:
    content = _read_index()
    assert marker in content, f"Expected '{marker}' in frontend/index.html"


@pytest.mark.xfail(reason="RunnerPlanTab not yet implemented in frontend/index.html", strict=True)
def test_runner_plan_tab_present() -> None:
    content = _read_index()
    assert "function RunnerPlanTab" in content


# ---------------------------------------------------------------------------
# HeavyTestsTab must NOT appear
# ---------------------------------------------------------------------------


def test_heavy_tests_tab_absent() -> None:
    content = _read_index()
    assert "HeavyTestsTab" not in content, "HeavyTestsTab was found — use TestsTab instead"


# ---------------------------------------------------------------------------
# TestsTab specifically (not HeavyTestsTab)
# ---------------------------------------------------------------------------


def test_tests_tab_function_present() -> None:
    content = _read_index()
    assert "function TestsTab" in content


def test_tests_tab_rerun_checks_response_ok_before_triggered_state() -> None:
    content = _read_index()
    rerun_start = content.index('fetch("/api/tests/rerun"')
    triggered_state = content.index('n[repo] = "triggered";', rerun_start)
    rerun_block = content[rerun_start:triggered_state]

    assert "if (!r.ok)" in rerun_block
    assert 'throw new Error("rerun failed")' in rerun_block


def test_runner_facing_tables_use_sortable_headers() -> None:
    content = _read_index()

    assert "function SortTh" in content
    assert "function sortRows" in content
    assert 'sortKey: "runner"' in content
    assert 'sortKey: "waiting"' in content
    assert 'sortKey: "machine"' in content
    assert 'sortKey: "when"' in content


def test_fleet_tab_has_mobile_kpi_and_status_filter_slice() -> None:
    content = _read_index()

    assert "fleet-mobile-kpis" in content
    assert "fleet-status-strip" in content
    assert "Runner status filters" in content
    assert 'filter === "online"' in content
    assert 'filter === "busy"' in content
    assert 'filter === "offline"' in content


# ---------------------------------------------------------------------------
# dangerouslySetInnerHTML safety check
# ---------------------------------------------------------------------------


def test_dangerous_set_inner_html_sanitized() -> None:
    """Every dangerouslySetInnerHTML usage must be paired with DOMPurify.sanitize,
    a known-safe wrapper (renderMarkdown), or an explicit safety comment."""
    lines = _index_lines()
    violations: list[int] = []
    for lineno, line in enumerate(lines):
        if "dangerouslySetInnerHTML" not in line:
            continue
        window_start = max(0, lineno - 5)
        window_end = min(len(lines), lineno + 6)
        window = "\n".join(lines[window_start:window_end])
        has_sanitize = "DOMPurify.sanitize" in window
        has_safe_wrapper = "renderMarkdown" in window  # renderMarkdown internally calls DOMPurify
        has_safe_comment = re.search(r"//.*safe|//.*sanitize|//.*trusted", window, re.IGNORECASE) is not None
        if not (has_sanitize or has_safe_wrapper or has_safe_comment):
            violations.append(lineno + 1)
    assert not violations, (
        f"dangerouslySetInnerHTML at line(s) {violations} has no DOMPurify.sanitize, "
        "renderMarkdown wrapper, or safety comment"
    )


def test_domPurify_loaded_in_html() -> None:
    """DOMPurify CDN script must be present so renderMarkdown can sanitize."""
    content = _read_index()
    assert "DOMPurify" in content, "DOMPurify must be loaded in index.html"


# ---------------------------------------------------------------------------
# Icons helper must expose I.play, I.flask, and I.activity
# ---------------------------------------------------------------------------


def test_icons_helper_has_play() -> None:
    content = _read_index()
    assert "I.play" in content


def test_icons_helper_has_flask() -> None:
    content = _read_index()
    assert "I.flask" in content


def test_icons_helper_has_activity() -> None:
    content = _read_index()
    assert "I.activity" in content


def test_issues_source_filter_persists_and_fetches_source_param() -> None:
    content = _read_index()
    assert "issuesSourceFilter" in content
    assert "/api/issues?limit=2000&source=" in content
    assert "/api/linear/workspaces" in content


def test_credentials_tab_supports_setting_linear_api_key() -> None:
    content = _read_index()
    assert "probe.key_provider && onSetKey" in content
    assert "/api/credentials/set-key" in content
    assert "Set API key" in content


# ---------------------------------------------------------------------------
# Single source of truth enforcement (issue #3)
# ---------------------------------------------------------------------------


def test_jsx_archive_removed() -> None:
    """RunnerDashboard.jsx must not exist — index.html is the sole canonical frontend.

    The JSX file was an unused reference copy that violated DRY. Removing it
    ensures feature logic lives in exactly one place.
    """
    jsx_path = _FRONTEND_DIR / "RunnerDashboard.jsx"
    assert not jsx_path.exists(), (
        "RunnerDashboard.jsx must not exist. "
        "frontend/index.html is the canonical frontend source. "
        "Do not re-introduce a second implementation."
    )


def test_index_html_is_only_frontend_source() -> None:
    """No other .jsx or .tsx files should exist in frontend/ at runtime."""
    unexpected = [p for p in _FRONTEND_DIR.iterdir() if p.suffix in {".jsx", ".tsx"} and p.is_file()]
    assert not unexpected, (
        f"Unexpected transpilable source files in frontend/: {[p.name for p in unexpected]}. "
        "frontend/index.html is the only runtime source — no build step exists."
    )
