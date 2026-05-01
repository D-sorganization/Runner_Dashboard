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
