from __future__ import annotations

from pathlib import Path


def test_maintenance_script_no_invalid_references() -> None:
    script_path = Path(__file__).resolve().parents[1] / "deploy" / "scheduled-dashboard-maintenance.sh"
    assert script_path.exists(), f"Script path does not exist: {script_path}"

    content = script_path.read_text(encoding="utf-8")

    # Verify that cancel_stale_queue.py is no longer referenced
    assert "cancel_stale_queue.py" not in content, "Found old cancel_stale_queue.py reference in maintenance script"

    # Verify that the curl payload uses min_age_minutes and NOT min_age
    assert "min_age_minutes" in content, "Did not find min_age_minutes in maintenance script"

    # Check that old "min_age" JSON parameter is not in the script
    assert '"min_age":' not in content, 'Found old "min_age": JSON parameter reference in maintenance script'
