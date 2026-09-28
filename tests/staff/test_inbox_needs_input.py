"""Work items waiting on the owner reach the inbox (#1734).

``_collect_needs_input`` read ``WorkItemRecord.run_id``, which does not exist
(runs live in ``links["runs"]``), so any waiting work item raised
AttributeError and the whole needs-input source failed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from staff import inbox
from staff.store import RunRecord, RunStore
from staff.work_items import WorkItemStore


def _waiting(store: WorkItemStore, title: str, runs: list[str]) -> str:
    wi = store.create_work_item(
        title=title,
        requested_by="dieter",
        links={"runs": runs, "issues": [], "prs": [], "code_requests": []},
    )
    store.transition_state(wi.id, "in_progress")
    store.transition_state(wi.id, "waiting_on_user")
    return wi.id


@pytest.mark.unit
def test_waiting_work_items_are_listed_once(tmp_path: Path) -> None:
    runs = RunStore(tmp_path / "runs.sqlite3")
    items = WorkItemStore(tmp_path / "runs.sqlite3")
    runs.create_run(
        RunRecord(
            id="run-asks",
            role="cartographer",
            provider="claude",
            model="sonnet",
            machine="TestNode",
            repo="AffineDrift",
            target_kind="prompt",
            target_ref="",
            prompt="map it",
            status="needs_input",
        )
    )
    standalone = _waiting(items, "Pick a charter owner", runs=[])
    _waiting(items, "Answer the cartographer", runs=["run-asks"])

    collected = inbox._collect_needs_input(runs, items)  # noqa: SLF001

    ids = [item.id for item in collected]
    assert f"waiting_wi_{standalone}" in ids
    assert "run-asks" in " ".join(ids)
    assert len([i for i in ids if i.startswith("waiting_wi_")]) == 1  # the run already covers the second item
