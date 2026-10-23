"""Role-policy failures must preserve the already-recorded run and verdict."""

from pathlib import Path

import pytest
from staff.store import RunRecord, RunStore
from staff.verification import Verdict, apply_deliverable_failure


@pytest.mark.parametrize("verification", ["unverified", "no_change", "failed"])
def test_role_resolution_error_does_not_crash_completion(tmp_path: Path, verification: str) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    rec = RunRecord(
        id="run-policy-error",
        role="night-watch",
        provider="fake",
        model=None,
        machine="TestNode",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="42",
        prompt="test",
        status="succeeded",
    )

    def broken_policy() -> bool:
        raise RuntimeError("role registry unavailable")

    assert (
        apply_deliverable_failure(store, rec, Verdict(verification, "existing evidence"), opens_pr=broken_policy) is rec
    )
    assert rec.status == "succeeded"
