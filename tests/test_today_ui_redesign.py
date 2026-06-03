"""Structural regression guards for the 2026-05-18 UI redesign series.

Pins the markup + CSS contracts introduced in PRs #672 (mock-quota
removal) and #673 (two-row header + fleet-hero panel + stat-sub clamp).

Static greps over the source files — no module imports, no JSX runtime.
Pure-text checks so they run on every platform.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_FRONTEND_SRC = _REPO / "frontend" / "src"
_APP_TSX = _FRONTEND_SRC / "legacy" / "App.tsx"
_INDEX_CSS = _FRONTEND_SRC / "index.css"
_LIB_FLEET_ALERTS = _FRONTEND_SRC / "lib" / "fleetAlerts.ts"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _read_runtime_source() -> str:
    parts: list[str] = []
    for path in sorted(_FRONTEND_SRC.rglob("*")):
        if path.suffix not in {".css", ".ts", ".tsx"}:
            continue
        if "__tests__" in path.relative_to(_FRONTEND_SRC).parts:
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


# ─── PR #672 regression guard: FLEET QUOTA stays removed ─────────────────────


def test_fleet_quota_widget_not_reintroduced() -> None:
    """The mock 'FLEET QUOTA 14/20' widget had no backing API and was
    obscuring the principal/settings controls. Removed in #672 —
    explicit guard so it can't be quietly re-added.

    The file is allowed to contain a comment EXPLAINING the removal; the
    assertion strips `//`-prefixed lines before grepping so the comment
    doesn't trigger a false positive.
    """
    src = _read(_APP_TSX)
    # Mock-data variables — these only exist if the widget is rendering
    assert "var quotaUsed" not in src, "mock quotaUsed variable reintroduced"
    assert "quotaTotal" not in src, "mock quotaTotal variable reintroduced"
    # Strip line comments before checking for the rendered widget label
    non_comment = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("//"))
    assert '"FLEET QUOTA"' not in non_comment, "the FLEET QUOTA widget string was reintroduced inside a render call"


def test_grad_quota_css_token_not_reintroduced() -> None:
    """The --grad-quota CSS custom property powered the mock widget's
    progress bar. With the widget gone, the token is unused — guard
    against zombie re-introduction."""
    src = _read(_INDEX_CSS)
    assert "--grad-quota" not in src


# ─── PR #673: two-row header ────────────────────────────────────────────────


def test_app_header_uses_two_row_variant() -> None:
    """The header was packing logo + 10 tabs + 6 status pills + 3
    actions into a 56px row, forcing horizontal scroll. The .app-header
    must opt into the .app-header--rows variant which lays out children
    in two stacked rows."""
    src = _read_runtime_source()
    assert '"app-header app-header--rows"' in src


def test_app_header_has_primary_and_secondary_rows() -> None:
    """The two-row split must use the documented class names so the CSS
    can target them. Renaming either side would break the layout."""
    src = _read_runtime_source()
    assert "app-header__row--primary" in src
    assert "app-header__row--secondary" in src


def test_index_css_defines_two_row_header_styles() -> None:
    """The CSS variant must define both rows with the correct sizing:
    primary 56px, secondary 44px+, secondary tinted."""
    src = _read(_INDEX_CSS)
    assert ".app-header.app-header--rows" in src
    assert ".app-header__row--primary" in src
    assert ".app-header__row--secondary" in src


def test_index_css_hides_tab_bar_scrollbar() -> None:
    """The tab-bar is still scrollable on overflow but the persistent
    grey scrollbar underneath the header was visual clutter. Hidden via
    -webkit-scrollbar height:0 + scrollbar-width:none."""
    src = _read(_INDEX_CSS)
    assert ".tab-bar::-webkit-scrollbar" in src
    assert "scrollbar-width: none" in src


# ─── PR #673: fleet-hero panel on Overview ───────────────────────────────────


def test_overview_renders_fleet_hero_section() -> None:
    """The Overview tab must render the fleet-hero section above the
    KPI grid. This is the user-facing 'system health' surface they
    asked for."""
    src = _read_runtime_source()
    assert '"fleet-hero fleet-hero--"' in src or '"fleet-hero"' in src
    assert "fleet-hero__status" in src
    assert "fleet-hero__kpis" in src
    assert "fleet-hero__alerts" in src


def test_fleet_hero_kpi_buttons_navigate_to_tabs() -> None:
    """The hero KPI tiles must be click-through buttons that navigate
    to the matching tab (machines, queue, overview). Just rendering
    static numbers misses the 'jump to detail' affordance."""
    src = _read_runtime_source()
    # The four KPI buttons each have an onClick: function () { setTab(...); }
    assert 'setTab("machines"); }' in src
    assert 'setTab("queue"); }' in src


def test_fleet_hero_uses_extracted_alerts_module() -> None:
    """The rollup logic must call the extracted, unit-tested
    fleetAlerts module rather than reintroducing inline rules in the
    legacy h()-tree. That's the whole point of the extraction."""
    src = _read_runtime_source()
    assert 'from "../lib/fleetAlerts"' in src
    assert "fleetAlerts.computeFleetAlerts" in src
    assert "fleetAlerts.fleetLevelLabel" in src


def test_fleet_alerts_module_exists_and_exports_contract() -> None:
    """The extracted module's public contract: computeFleetAlerts,
    fleetLevelLabel, FleetAlert, FleetState, FleetLevel."""
    src = _read(_LIB_FLEET_ALERTS)
    for export in [
        "export function computeFleetAlerts",
        "export function fleetLevelLabel",
        "export interface FleetAlert",
        "export interface FleetState",
        "export type FleetLevel",
    ]:
        assert export in src, f"missing export: {export}"


def test_index_css_defines_fleet_hero_severity_borders() -> None:
    """The hero panel's left border colour reflects the worst severity
    (green/yellow/red). Pin the three CSS classes so a severity
    semantic change doesn't quietly drop a level."""
    src = _read(_INDEX_CSS)
    assert ".fleet-hero--ok" in src
    assert ".fleet-hero--warning" in src
    assert ".fleet-hero--critical" in src


# ─── PR #673: bounded stat-sub line-clamp ───────────────────────────────────


def test_stat_sub_has_line_clamp() -> None:
    """A 200+ char Windows scheduled-task error in the keepalive
    sub-line was blowing out the card height. CSS line-clamp(2) keeps
    every Stat card uniform; full text remains accessible via title."""
    src = _read(_INDEX_CSS)
    assert ".stat-sub" in src
    assert "-webkit-line-clamp: 2" in src or "line-clamp: 2" in src


def test_stat_component_supports_subtitle_for_truncation_hover() -> None:
    """When the visible sub line has been truncated, the full text must
    still be reachable via the title attribute. The Stat component
    accepts a subTitle prop for exactly this case."""
    src = _read_runtime_source()
    assert "subTitle" in src
    assert "title: subTitle" in src or "title: p.subTitle" in src


def test_keepalive_card_passes_subtitle_with_full_message() -> None:
    """The WSL Keepalive card must pass the FULL watchdog message via
    subTitle so the truncated visible text can be expanded on hover."""
    src = _read_runtime_source()
    # Find the WSL Keepalive Stat block and check subTitle is supplied
    # within ~30 lines of "label: \"WSL Keepalive\""
    idx = src.find('label: "WSL Keepalive"')
    assert idx >= 0, "WSL Keepalive Stat not found"
    block = src[idx : idx + 1500]
    assert "subTitle: watchdog.detail" in block, "WSL Keepalive must pass full watchdog.detail/summary as subTitle"
