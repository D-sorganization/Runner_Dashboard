from __future__ import annotations

import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGET_SCRIPT = ROOT / "scripts" / "check_frontend_perf_budget.py"
SPEC = spec_from_file_location("check_frontend_perf_budget", BUDGET_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
budget_check = module_from_spec(SPEC)
sys.modules[SPEC.name] = budget_check
SPEC.loader.exec_module(budget_check)


def test_frontend_perf_budget_contract_is_present_and_locked() -> None:
    budget = budget_check.load_budget()

    assert budget_check.validate_budget_contract(budget) == []


def test_frontend_single_file_gzip_sizes_stay_within_interim_budget() -> None:
    budget = budget_check.load_budget()
    sizes = budget_check.measure_frontend()

    assert budget_check.validate_interim_sizes(budget, sizes) == []


def test_frontend_perf_budget_script_runs_from_checkout_root() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_frontend_perf_budget.py", "--json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_tab_chunk_budget_contract_enforced() -> None:
    """Per-route lazy chunk gzip must not exceed tab_chunk budget (issue #383)."""
    budget = budget_check.load_budget()
    tab_limit = budget.get("budgets", {}).get("tab_chunk", {}).get("js_gzip_bytes")
    assert tab_limit == 102400, f"tab_chunk.js_gzip_bytes must be 102400, got {tab_limit!r}"


# ---------------------------------------------------------------------------
# Real built-bundle enforcement (issue #831)
# ---------------------------------------------------------------------------


def test_built_bundle_budget_section_present() -> None:
    """The budget declares a real-bundle section with integer limits (#831)."""
    budget = budget_check.load_budget()
    bundle = budget.get("budgets", {}).get("built_bundle", {})
    assert isinstance(bundle.get("entry_js_gzip_bytes"), int)
    assert isinstance(bundle.get("per_chunk_js_gzip_bytes"), int)


def test_validate_built_bundle_reports_missing_build() -> None:
    """With no built artifact, the check fails loudly rather than passing."""
    budget = budget_check.load_budget()
    measured = {
        "entry_chunk": None,
        "entry_js_gzip_bytes": 0,
        "vendor_chunks": {},
        "app_chunks": {},
    }
    errors = budget_check.validate_built_bundle(budget, measured)
    assert errors, "missing build must produce an error"
    assert any("no built bundle" in e for e in errors)


def test_validate_built_bundle_passes_when_split() -> None:
    """A code-split bundle (small entry, App in its own chunk) is within budget."""
    budget = budget_check.load_budget()
    measured = {
        "entry_chunk": "index-abc.js",
        "entry_js_gzip_bytes": 53_000,
        "vendor_chunks": {"vendor-react-x.js": 60_000},
        "app_chunks": {"App-x.js": 74_000},
    }
    assert budget_check.validate_built_bundle(budget, measured) == []


def test_validate_built_bundle_fails_on_eager_monolith() -> None:
    """Regression guard: folding the legacy App back into the entry chunk
    (eager `import App`) pushes the entry past budget and must fail (#831)."""
    budget = budget_check.load_budget()
    # ~120KB gzip — the pre-split eager monolith entry size.
    measured = {
        "entry_chunk": "index-eager.js",
        "entry_js_gzip_bytes": 120_000,
        "vendor_chunks": {"vendor-react-x.js": 60_000},
        "app_chunks": {},
    }
    errors = budget_check.validate_built_bundle(budget, measured)
    assert errors, "eager monolith entry must exceed the entry budget"
    assert any("entry chunk" in e for e in errors)


def test_validate_built_bundle_fails_on_oversized_app_chunk() -> None:
    """Any non-vendor async chunk over the per-chunk budget fails."""
    budget = budget_check.load_budget()
    measured = {
        "entry_chunk": "index-x.js",
        "entry_js_gzip_bytes": 50_000,
        "vendor_chunks": {},
        "app_chunks": {"App-huge.js": 200_000},
    }
    errors = budget_check.validate_built_bundle(budget, measured)
    assert any("App-huge.js" in e for e in errors)


def test_vendor_chunks_excluded_from_per_chunk_budget() -> None:
    """Large shared vendor chunks are excluded from the per-chunk app budget."""
    budget = budget_check.load_budget()
    measured = {
        "entry_chunk": "index-x.js",
        "entry_js_gzip_bytes": 50_000,
        # A vendor chunk larger than per_chunk budget must NOT fail the check.
        "vendor_chunks": {"vendor-react-x.js": 200_000},
        "app_chunks": {},
    }
    assert budget_check.validate_built_bundle(budget, measured) == []


def test_find_entry_chunk_parses_built_index() -> None:
    """The entry chunk is discovered from the built index.html module script."""
    import tempfile

    html = (
        "<!doctype html><html><head>"
        '<script type="module" crossorigin src="/assets/index-DEADBEEF.js"></script>'
        '<link rel="modulepreload" href="/assets/vendor-react-x.js">'
        "</head><body></body></html>"
    )
    with tempfile.TemporaryDirectory() as tmp:
        idx = Path(tmp) / "index.html"
        idx.write_text(html, encoding="utf-8")
        assert budget_check.find_entry_chunk(idx) == "index-DEADBEEF.js"
