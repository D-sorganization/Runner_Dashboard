"""Static source integrity checks for frontend/index.html.

These tests do not execute JavaScript — they assert that the compiled single-file
frontend contains the structural markers expected by the dashboard design contract.
"""

from __future__ import annotations  # noqa: E402

import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
_HTML_SHELL = _FRONTEND_DIR / "index.html"
_SRC_DIR = _FRONTEND_DIR / "src"
_INDEX_HTML = _SRC_DIR / "legacy" / "App.tsx"
_FETCH_GUARDS = _SRC_DIR / "legacy" / "fetchGuards.ts"
_VISIBLE_INTERVAL = _SRC_DIR / "legacy" / "visibleInterval.ts"
_QUEUE_INDEX = _SRC_DIR / "pages" / "Queue" / "index.tsx"
_FLEET_TAB = _SRC_DIR / "pages" / "FleetTab.tsx"
_REMEDIATION_TAB = _SRC_DIR / "pages" / "RemediationTab.tsx"
_PUSH_SETTINGS = _FRONTEND_DIR / "src" / "pages" / "PushSettings.tsx"
_DESIGN_DIR = _FRONTEND_DIR / "src" / "design"
_PRIMITIVES_DIR = _FRONTEND_DIR / "src" / "primitives"
_LEGACY_APP_LINE_RATCHET = 2886


def _read_index() -> str:
    """Return concatenated runtime frontend source.

    The dashboard has been decomposed from one legacy/App.tsx monolith into
    Vite source modules. Static integrity checks should follow the runtime
    source graph rather than a stale single-file assumption.
    """
    parts: list[str] = []
    for path in sorted(_SRC_DIR.rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        rel_parts = set(path.relative_to(_SRC_DIR).parts)
        if "__tests__" in rel_parts:
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _read_html_shell() -> str:
    return _HTML_SHELL.read_text(encoding="utf-8")


def _read_legacy_app() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _index_lines() -> list[str]:
    return _read_index().splitlines()


def _read_css() -> str:
    return (_FRONTEND_DIR / "src" / "index.css").read_text(encoding="utf-8")


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
    "function ScheduledJobs",
    "class LocalAppsTab",
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
    rerun_start = content.index('legacyFetch("/api/tests/rerun"')
    triggered_state = content.index('[repo]: "triggered"', rerun_start)
    rerun_block = content[rerun_start:triggered_state]

    assert "if (!r.ok)" in rerun_block
    assert 'throw new Error("rerun failed")' in rerun_block


def test_legacy_polling_pauses_while_tab_is_hidden() -> None:
    content = _read_legacy_app()
    helper = _VISIBLE_INTERVAL.read_text(encoding="utf-8")

    assert 'import { createVisibleInterval } from "./visibleInterval"' in content
    assert "document.hidden" in helper
    assert 'document.addEventListener("visibilitychange", onVisibilityChange)' in helper
    assert 'document.removeEventListener("visibilitychange", onVisibilityChange)' in helper
    assert "var t1 = setInterval" not in content
    assert "var healthInterval = setInterval" not in content

    poller_start = content.index("var cleanupIntervals = [")
    poller_block = content[poller_start : content.index("];", poller_start)]
    assert poller_block.count("createVisibleInterval(") == 15
    assert "createVisibleInterval(checkHealth, 2000)" in content


def test_legacy_app_line_count_ratchet_shrinks_for_issue_949() -> None:
    """The legacy monolith must only shrink while tabs are retired (#949)."""
    line_count = len(_INDEX_HTML.read_text(encoding="utf-8").splitlines())

    assert line_count <= _LEGACY_APP_LINE_RATCHET


def test_fleet_and_remediation_tabs_are_extracted_from_legacy_monolith() -> None:
    """Fleet and Remediation route through page modules, not inline twins (#949)."""
    content = _read_legacy_app()
    fleet_page = _FLEET_TAB.read_text(encoding="utf-8")
    remediation_page = _REMEDIATION_TAB.read_text(encoding="utf-8")

    assert 'import { FleetTab } from "../pages/FleetTab"' in content
    assert 'import { RemediationTab } from "../pages/RemediationTab"' in content
    assert "function FleetTab" not in content
    assert "function RemediationTab" not in content
    assert "h(FleetTab, {" in content
    assert "h(RemediationTab, {" in content
    assert "export function FleetTab" in fleet_page
    assert "export function RemediationTab" in remediation_page


def test_runner_facing_tables_use_sortable_headers() -> None:
    content = _read_index()

    assert "function SortTh" in content
    assert "function sortRows" in content
    assert 'sortKey: "runner"' in content
    assert 'sortKey: "waiting"' in content or 'sortKey="waiting"' in content
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


def test_queue_tab_has_mobile_health_cards_and_confirmed_cancel_slice() -> None:
    content = _read_index()

    assert "Queue health summary" in content
    assert "Stale queued runs" in content
    assert "mobile-run-card" in content
    assert "Confirm cancel (1)" in content
    assert "waitingSeconds(r) > 300" in content


def test_workflows_filters_persist_in_session_storage() -> None:
    content = _read_index()

    assert "workflowsMobileFilters" in content
    assert "sessionStorage.getItem" in content
    assert "sessionStorage.setItem" in content
    assert "mobile-workflow-filters" in content


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


def test_mobile_credentials_tab_is_locked_and_webauthn_gated() -> None:
    content = _read_index()
    assert "mobile-credentials-lock" in content
    assert "Show credentials" in content
    assert "/api/auth/webauthn/assert/begin" in content
    assert "/api/auth/webauthn/assert/complete" in content
    assert 'userVerification: "required"' in content
    assert "Credentials re-locked after 60 seconds." in content
    assert "Credentials re-locked when the tab lost focus." in content
    assert "mobileCredentialsViewport" in content
    assert "if (!mobileCredentialsViewport) fetchCredentials();" in content


def test_mobile_credentials_mutations_require_bottom_sheet_confirmation() -> None:
    content = _read_index()
    assert "mobile-credentials-sheet" in content
    assert "Confirm sensitive operation" in content
    assert "Continue only if you intend to change credential state." in content
    assert "setMobileConfirmProbe(probe)" in content
    assert "onSetKey(probe)" in content


def test_credentials_api_is_excluded_from_frontend_cache_contract() -> None:
    content = _FETCH_GUARDS.read_text(encoding="utf-8")
    legacy_app = _read_legacy_app()
    assert "SERVICE_WORKER_CACHE_DENYLIST" in content
    assert "/^\\/api\\/credentials(?:\\/|$)/" in content
    assert "shouldBypassServiceWorkerCache(url)" in content
    assert 'cache: "no-store"' in content
    assert "installLegacyFetchGuards" in legacy_app
    assert "navigator.serviceWorker" not in content


def test_mobile_remediation_three_tap_slice_markers_present() -> None:
    content = _read_index()
    assert "remediation-mobile-tabs" in content
    assert "mobile-remediation-sheet" in content
    assert "Dispatch submitted for " in content
    assert "Waiting for agent heartbeat." in content
    assert "Open on desktop" in content
    assert "Preview safety plan" in content


def test_maxwell_mobile_chat_slice_markers_present() -> None:
    content = _read_index()
    assert "maxwellMobileChatHistory" in content
    assert "maxwell-chat-messages" in content
    assert "maxwell-composer" in content
    assert "/api/maxwell/chat" in content
    assert "which runners are blocked?" in content
    assert "Maxwell-Daemon is unreachable. Chat history is preserved" in content
    assert "TextDecoder" in content


def test_mobile_read_mostly_reports_assessments_feature_requests_markers_present() -> None:
    content = _read_index()
    assert "reports-shell" in content
    assert "reports-sidebar" in content
    assert "reports-reader" in content
    assert "report-open-raw" in content
    assert "assessment-mobile-card-list" in content
    assert "assessment-mobile-score" in content
    assert "feature-request-mobile-list" in content
    assert "feature-request-mobile-card" in content
    assert "Feature request history" in content
    assert "requestVoteCount" in content


def test_mobile_a11y_hit_target_token_is_enforced() -> None:
    content = _read_css()

    assert "--mobile-hit-target: 44px;" in content
    assert "@media (max-width: 768px)" in content
    for selector in [
        ".btn",
        ".tab-btn",
        ".subtab",
        ".report-item",
        ".form-input",
        ".form-select",
        ".search-bar",
        ".mobile-workflow-filters input",
        ".mobile-workflow-filters select",
    ]:
        assert selector in content
    assert content.count("min-height: var(--mobile-hit-target);") >= 2


def test_mobile_viewport_meta_allows_user_zoom() -> None:
    content = _read_html_shell()

    assert 'name="viewport"' in content
    assert 'content="width=device-width, initial-scale=1.0"' in content
    assert "maximum-scale" not in content
    assert "user-scalable=no" not in content


def test_mobile_a11y_reduced_motion_contract_is_static_guarded() -> None:
    css = _read_css()
    js = _read_index()

    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "animation-duration: 0.01ms !important;" in css
    assert "animation-iteration-count: 1 !important;" in css
    assert "transition-duration: 0.01ms !important;" in css
    assert "function prefersReducedMotion()" in js
    assert 'window.matchMedia("(prefers-reduced-motion: reduce)")' in js
    assert 'transition: prefersReducedMotion() ? "none"' in js


def test_mobile_a11y_dialogs_and_sections_are_labelled() -> None:
    content = _read_index()

    for label in [
        "Runner status filters",
        "Mobile runner monitoring cards",
        "Queue health summary",
        "Stale queued runs",
        "Mobile remediation dispatch",
        "Confirm mobile credential change",
        "Feature request history",
    ]:
        assert f'"aria-label": "{label}"' in content or f'aria-label="{label}"' in content
    assert content.count('"aria-modal": "true"') >= 2


def test_mobile_design_token_modules_exist() -> None:
    for name in ["tokens.ts", "breakpoints.ts", "type.ts", "motion.ts"]:
        assert (_DESIGN_DIR / name).exists(), f"Missing mobile design module {name}"


def test_mobile_design_tokens_mirror_runtime_css_contract() -> None:
    runtime = _read_css()
    tokens = (_DESIGN_DIR / "tokens.ts").read_text(encoding="utf-8")

    for css_name, value in [
        ("--bg-primary", "#0f1117"),
        ("--bg-secondary", "#161b22"),
        ("--bg-card", "#1c2128"),
        ("--text-primary", "#e6edf3"),
        ("--text-secondary", "#8b949e"),
        ("--accent-blue", "#58a6ff"),
        ("--accent-green", "#3fb950"),
        ("--accent-red", "#f85149"),
        ("--mobile-hit-target", "44px"),
    ]:
        assert f"{css_name}: {value};" in runtime
        assert value in tokens

    assert "comfortableHitTarget" in tokens
    assert "48px" in tokens
    assert "toCssVariables" in tokens


def test_mobile_breakpoint_and_motion_contract_modules_are_static_guarded() -> None:
    breakpoints = (_DESIGN_DIR / "breakpoints.ts").read_text(encoding="utf-8")
    motion = (_DESIGN_DIR / "motion.ts").read_text(encoding="utf-8")

    for marker in ["xs", "sm", "md", "lg", "xl", "isMobile", "useBreakpoint", "375", "412"]:
        assert marker in breakpoints
    for marker in [
        "prefers-reduced-motion: reduce",
        "animation-duration: 0.01ms !important;",
        "transition-duration: 0.01ms !important;",
        "prefersReducedMotion",
        'window.matchMedia("(prefers-reduced-motion: reduce)")',
    ]:
        assert marker in motion


def test_mobile_design_docs_cover_native_shell_and_tokens() -> None:
    docs_dir = Path(__file__).parent.parent / "docs"
    native_shell = (docs_dir / "mobile-native-shell.md").read_text(encoding="utf-8")
    design_system = (docs_dir / "mobile-design-system.md").read_text(encoding="utf-8")

    assert "Capacitor" in native_shell
    assert "React Native is not the preferred path" in native_shell
    assert "Go/No-Go Criteria" in native_shell
    assert "375x812" in design_system
    assert "412x915" in design_system
    assert "--mobile-hit-target" in design_system


def test_mobile_design_modules_do_not_introduce_runtime_components() -> None:
    for path in _DESIGN_DIR.glob("*.ts"):
        content = path.read_text(encoding="utf-8")
        assert "style={" not in content
        assert "React" not in content


def test_touch_primitives_foundation_contract_is_static_guarded() -> None:
    touch_button = (_PRIMITIVES_DIR / "TouchButton.tsx").read_text(encoding="utf-8")
    segmented = (_PRIMITIVES_DIR / "SegmentedControl.tsx").read_text(encoding="utf-8")
    exports = (_PRIMITIVES_DIR / "index.ts").read_text(encoding="utf-8")
    css = _read_css()
    docs = (Path(__file__).parent.parent / "docs" / "mobile-design-system.md").read_text(
        encoding="utf-8",
    )

    assert 'data-touch-primitive="TouchButton"' in touch_button
    assert "aria-pressed={pressed}" in touch_button
    assert 'type = "button"' in touch_button
    assert 'data-touch-primitive="SegmentedControl"' in segmented
    assert 'role="radiogroup"' in segmented
    assert 'role="radio"' in segmented
    assert "aria-checked={option.value === value}" in segmented
    assert "ArrowRight" in segmented and "ArrowLeft" in segmented
    assert 'export { TouchButton } from "./TouchButton";' in exports
    assert 'export { SegmentedControl } from "./SegmentedControl";' in exports
    assert ".touch-button" in css
    assert ".segmented-control" in css
    assert "min-height: var(--mobile-hit-target);" in css
    assert "TouchButton" in docs
    assert "SegmentedControl" in docs
    assert "SwipeRow" in docs and "PullToRefresh" in docs and "BottomSheet" in docs


def test_fleet_tab_has_mobile_runner_monitoring_cards() -> None:
    content = _read_index()

    for marker in [
        "fleet-mobile-runner-list",
        "mobile-runner-card",
        "mobile-runner-meter-row",
        "mobile-runner-meter-fill",
        "runner-fleet-desktop-list",
        "function machineTelemetryForRunner",
        "function runnerCurrentRun",
        "function compactRunnerActivity",
        "compactRunnerActivity(currentRun)",
        "dashboard live",
        "runners only",
    ]:
        assert marker in content

    assert "runnerCurrentRun(r, p.runs || [])" in content
    assert 'node.last_seen ? timeAgo(node.last_seen) : "not seen"' in content
    assert "new Date(node.last_seen).toLocaleString" not in content


def test_router_is_the_single_nav_source_of_truth() -> None:
    """main.tsx mounts React Router as the one nav source (issues #835, #831).

    The previous hand-rolled `window.location.pathname` + AppWithMobileShell nav
    has been retired: tabs are now real, deep-linkable routes. The push-settings
    deep link is preserved as an explicit Route, and the URL<->tab mapping lives
    in shell/routing.ts.
    """
    main_tsx = (_FRONTEND_DIR / "src" / "main.tsx").read_text(encoding="utf-8")

    assert _PUSH_SETTINGS.exists()
    for marker in [
        "PushSettings",
        "BrowserRouter",
        "<Routes>",
        '<Route path="/settings/push"',
        '<Route path="/t/:tabId"',
        "RoutedShell",
    ]:
        assert marker in main_tsx, f"missing router marker: {marker!r}"

    # The retired hand-rolled navigation must not reappear in the entry.
    assert "AppWithMobileShell" not in main_tsx
    assert "isPushSettingsRoute(window.location.pathname) ? <PushSettings />" not in main_tsx

    # The unmounted parallel router was retired in favour of the mounted one.
    assert not (_FRONTEND_DIR / "src" / "router.tsx").exists()

    # The URL<->tab mapping is the single source of truth for routing.
    routing_ts = (_FRONTEND_DIR / "src" / "shell" / "routing.ts").read_text(encoding="utf-8")
    for marker in ["pathnameToTabId", "tabIdToPath", "isPushSettingsRoute"]:
        assert marker in routing_ts, f"missing routing helper: {marker!r}"


def test_queue_desktop_route_bypasses_legacy_app() -> None:
    """The Queue desktop tab is self-owned and must not remount legacy/App.tsx.

    QueueTab already fetches `/api/queue` when rendered without legacy-owned
    props. Keep the modern desktop shell pointed at the extracted page so #949
    continues shrinking the legacy fallback surface instead of expanding it.
    """
    routed_shell = (_FRONTEND_DIR / "src" / "shell" / "RoutedShell.tsx").read_text(
        encoding="utf-8",
    )
    queue_page = (_FRONTEND_DIR / "src" / "pages" / "Queue" / "index.tsx").read_text(
        encoding="utf-8",
    )

    assert 'case "queue":' in routed_shell
    assert "return <QueueTab />;" in routed_shell
    assert 'fetch("/api/queue")' in queue_page
    assert "p.queue ?? localQueue ?? {}" in queue_page


def test_org_desktop_route_bypasses_legacy_app() -> None:
    """The Organization desktop tab owns its data outside legacy/App.tsx (#949)."""
    routed_shell = (_FRONTEND_DIR / "src" / "shell" / "RoutedShell.tsx").read_text(
        encoding="utf-8",
    )
    org_page = (_FRONTEND_DIR / "src" / "pages" / "Org.tsx").read_text(
        encoding="utf-8",
    )

    assert 'case "org":' in routed_shell
    assert "return <OrgPage />;" in routed_shell
    assert 'legacyFetch("/api/repos"' in org_page
    assert 'legacyFetch("/api/stats"' in org_page
    assert "export function OrgPage" in org_page


def test_tests_desktop_route_bypasses_legacy_app() -> None:
    """The Tests desktop tab owns its data outside legacy/App.tsx (#949)."""
    routed_shell = (_FRONTEND_DIR / "src" / "shell" / "RoutedShell.tsx").read_text(
        encoding="utf-8",
    )
    tests_page = (_FRONTEND_DIR / "src" / "pages" / "TestsPage.tsx").read_text(
        encoding="utf-8",
    )

    assert 'case "tests":' in routed_shell
    assert "return <TestsPage />;" in routed_shell
    assert 'legacyFetch("/api/heavy-tests/repos"' in tests_page
    assert 'legacyFetch("/api/tests/ci-results"' in tests_page
    assert "export function TestsPage" in tests_page


def test_runner_schedule_desktop_route_bypasses_legacy_app() -> None:
    """The Runner Schedule desktop tab owns its data outside legacy/App.tsx (#949)."""
    routed_shell = (_FRONTEND_DIR / "src" / "shell" / "RoutedShell.tsx").read_text(
        encoding="utf-8",
    )
    schedule_page = (_FRONTEND_DIR / "src" / "pages" / "RunnerSchedule.tsx").read_text(
        encoding="utf-8",
    )

    assert 'case "runner-schedule":' in routed_shell
    assert "return <RunnerSchedulePage />;" in routed_shell
    assert 'legacyFetch("/api/fleet/schedule"' in schedule_page
    assert 'method: "POST"' in schedule_page
    assert "export function RunnerSchedulePage" in schedule_page


def test_workflows_desktop_route_bypasses_legacy_app() -> None:
    """The Workflows desktop tab owns list/dispatch data outside legacy/App.tsx (#949)."""
    routed_shell = (_FRONTEND_DIR / "src" / "shell" / "RoutedShell.tsx").read_text(
        encoding="utf-8",
    )
    workflows_page = (_FRONTEND_DIR / "src" / "pages" / "WorkflowsPage.tsx").read_text(
        encoding="utf-8",
    )

    assert 'case "workflows":' in routed_shell
    assert "return <WorkflowsPage />;" in routed_shell
    assert 'legacyFetch("/api/workflows/list"' in workflows_page
    assert 'legacyFetch("/api/workflows/dispatch"' in workflows_page
    assert "export function WorkflowsPage" in workflows_page


def test_main_tsx_has_root_suspense_fallback() -> None:
    main_tsx = (_FRONTEND_DIR / "src" / "main.tsx").read_text(encoding="utf-8")
    assert "<React.Suspense" in main_tsx
    assert 'role="status"' in main_tsx
    assert 'aria-label="Loading dashboard"' in main_tsx


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
