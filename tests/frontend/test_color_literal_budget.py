"""
CI guard — D5 / issue #706.

Counts rgba( occurrences in frontend/src/index.css and enforces a hard-coded
budget so new status tokens must be introduced via CSS custom properties
(--status-*) rather than inline rgba() literals scattered through component
files.

The budget is a hard-coded constant that represents the approved baseline
established when D5 landed. It does NOT auto-recalculate from the current
file — that would make the test tautological and defeat its purpose.

To lower the budget: refactor rgba() usages to CSS custom properties and
update RGBA_BUDGET to the new count.
To raise the budget: get explicit approval from maintainers and update
RGBA_BUDGET with a comment explaining the exception.
"""

import pathlib
import re

CSS_PATH = pathlib.Path("frontend/src/index.css")

# ---------------------------------------------------------------------------
# Issue #826 — raw hex literal guard for .tsx components.
#
# Colours in .tsx must flow through the CSS custom-property token system
# (var(--accent-*), var(--badge-*), var(--text-*) …) so a theme is a data-only
# edit. Raw `"#rrggbb"` / `'#rgb'` literals freeze a colour to one theme and
# bypass re-tinting.
#
# This is a *budget* guard (mirrors the rgba() budget above): the frozen
# baseline is the count of hex literals that remained after the #826
# migration. New raw hex pushes the count over budget and fails CI. To lower
# the budget, migrate a literal to a token and decrement HEX_BUDGET.
#
# Allowlisted by design (NOT counted): `LANG_COLORS` maps, which encode GitHub's
# canonical per-language brand colours and are intentionally theme-independent.
# ---------------------------------------------------------------------------

TSX_ROOT = pathlib.Path("frontend/src")

# Quoted 3/6/8-digit hex colour literal. Quoting excludes issue refs like
# "(#826)" in comments, which are not quoted colour strings.
_HEX_LITERAL = re.compile(r"""['"]#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})['"]""")

# LANG_COLORS object literal — allowlisted brand colours, stripped before counting.
_LANG_COLORS_BLOCK = re.compile(r"LANG_COLORS\s*[:=][^{]*\{.*?\}", re.DOTALL)

# Frozen baseline after the #826 migration (LabelGuide phantom-token remap +
# legacy/App.tsx badge map + #fff-on-accent literals migrated to tokens).
# 2026-06-02 (#826): established at 35. Lower this as remaining literals in
# RootErrorBoundary / ThemeSettings / RunnerCard etc. are migrated.
HEX_BUDGET = 35


def _count_hex_literals() -> dict[str, int]:
    """Return {relative_path: hex_count} for every .tsx file, LANG_COLORS excluded.

    Postcondition: counts exclude allowlisted LANG_COLORS brand maps.
    """
    counts: dict[str, int] = {}
    for path in sorted(TSX_ROOT.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        text = _LANG_COLORS_BLOCK.sub("", text)
        n = len(_HEX_LITERAL.findall(text))
        if n:
            counts[path.as_posix()] = n
    return counts


def test_tsx_hex_literal_budget() -> None:
    """Ensure raw hex colour literals in .tsx do not exceed the frozen budget.

    Precondition: TSX_ROOT exists.
    Postcondition: total quoted hex literals (excluding LANG_COLORS) <= HEX_BUDGET.
    """
    assert TSX_ROOT.is_dir(), f"TSX source root not found: {TSX_ROOT}"
    counts = _count_hex_literals()
    total = sum(counts.values())
    breakdown = "\n".join(f"  {n:3d}  {p}" for p, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    assert total <= HEX_BUDGET, (
        f"Too many raw hex colour literals in .tsx: {total} > {HEX_BUDGET}.\n"
        "Migrate colours to CSS custom-property tokens "
        "(var(--accent-*), var(--badge-*), var(--text-*)) instead of inline hex, "
        f"or — if intentional brand colour — add it to an allowlisted LANG_COLORS map.\n"
        f"Per-file breakdown:\n{breakdown}"
    )


def test_legacy_app_badge_map_uses_tokens() -> None:
    """Regression for #826: the legacy App.tsx issue-badge maps are tokenised.

    The getTypeStyle / getComplexityStyle / getJudgementStyle colour maps must
    not reintroduce raw rgba()/hex; they must reference var(--badge-*) tokens.
    """
    app = pathlib.Path("frontend/src/legacy/App.tsx").read_text(encoding="utf-8")
    # The badge helpers reference semantic tokens.
    assert "var(--badge-neutral-bg)" in app, "badge map lost its --badge-* tokens"
    assert "var(--text-on-accent)" in app, "#fff-on-accent literals not tokenised"
    # No quoted hex colour survives outside the allowlisted LANG_COLORS map.
    stripped = _LANG_COLORS_BLOCK.sub("", app)
    leftover = _HEX_LITERAL.findall(stripped)
    assert not leftover, f"legacy/App.tsx still has raw hex outside LANG_COLORS: {leftover}"


def test_label_guide_has_no_phantom_tokens() -> None:
    """Regression for #826: LabelGuide must not reference undefined token names.

    The phantom namespace (--surface-alt / --surface / --danger / --accent /
    --success) was never defined in index.css, so the component always rendered
    on its hardcoded fallbacks. It must now use the real token names.
    """
    guide = pathlib.Path("frontend/src/pages/Fleet/LabelGuide.tsx").read_text(encoding="utf-8")
    phantom = [
        "var(--surface-alt",
        "var(--surface,",
        "var(--surface )",
        "var(--danger",
        "var(--accent,",
        "var(--accent )",
        "var(--success",
    ]
    found = [tok for tok in phantom if tok in guide]
    assert not found, f"LabelGuide still references phantom tokens: {found}"


# Hard-coded baseline established at D5 landing (issue #706).
# Lowering this over time is encouraged as literals are migrated
# to semantic custom properties (--status-*, --badge-*, --glass-*).
#
# 2026-06-01 (PR #851, theming polish): raised 73 -> 86. The +13 rgba()
# literals are NOT scattered inline colours — they are the rgba layers of the
# new elevation shadow tokens (--shadow-soft/--shadow-card/--shadow-modal),
# defined once per theme inside :root and consumed everywhere via
# var(--shadow-*). This is exactly the semantic-token pattern this guard
# promotes, so the baseline is raised to cover them. Maintainer-approved.
# 2026-06-02 (#826, token migration): lowered 86 -> 84. The --status-{healthy,
# warning,critical,info}-bg tints were de-duplicated to alias the matching
# --badge-*-bg tokens (removing 4 rgba() literals); --badge-purple-bg added 1.
RGBA_BUDGET = 84


def test_rgba_budget() -> None:
    """Ensure rgba() literal count in index.css does not exceed the approved budget.

    Precondition: CSS_PATH exists and is readable.
    Postcondition: the count of rgba( in the file is at most RGBA_BUDGET.
    """
    assert CSS_PATH.exists(), f"CSS file not found: {CSS_PATH}"
    css = CSS_PATH.read_text(encoding="utf-8")
    count = len(re.findall(r"rgba\(", css))
    assert count <= RGBA_BUDGET, (
        f"Too many hardcoded rgba() literals in {CSS_PATH}: {count} > {RGBA_BUDGET}. "
        "Introduce new colours via CSS custom properties in :root instead, "
        f"or obtain maintainer approval to raise RGBA_BUDGET (currently {RGBA_BUDGET})."
    )
