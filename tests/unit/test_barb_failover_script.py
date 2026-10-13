"""The manual Barb failover script (#1800, BR-06; runbook docs/operations/barb-authority-failover.md).

Offline and headless: every subcommand works on a local SQLite file, prints one JSON document and returns
0 on success, 1 when the operation is refused, 2 on a usage error.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from staff.conversations import ConversationStore
from staff.run_link import run_card_id

from scripts import barb_failover


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    code = barb_failover.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out)


@pytest.fixture
def old_authority(tmp_path: Path) -> Path:
    path = tmp_path / "ct" / "conversations.sqlite3"
    path.parent.mkdir()
    store = ConversationStore(path)
    tid = store.create_thread(title="t", kind="direct", role="librarian", created_by="user:d").id
    store.apply_run_card_update(
        thread_id=tid,
        run_id="run-1",
        message_id=run_card_id("run-1"),
        status="succeeded",
        version=1,
        body_md="done",
        meta={"run_id": "run-1", "status": "succeeded"},
        author="librarian",
        authority_epoch=1,
        authority_node="CT",
    )
    store.close()
    return path


@pytest.mark.unit
def test_status_reports_the_fence(old_authority: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(capsys, "status", "--db", str(old_authority))
    assert code == 0
    assert out["fence"]["epoch"] == 1 and out["fence"]["node"] == "CT"


@pytest.mark.unit
def test_full_failover_sequence(old_authority: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(capsys, "backup", "--db", str(old_authority))
    assert code == 0
    backup = Path(out["backup"])
    assert backup.is_file()

    new_db = tmp_path / "desk" / "conversations.sqlite3"
    new_db.parent.mkdir()
    shutil.copyfile(backup, new_db)  # the operator copies the single backup file to the new authority

    code, out = _run(capsys, "advance", "--db", str(new_db), "--epoch", "2", "--node", "DESK", "--reason", "CT down")
    assert code == 0 and out["fence"]["epoch"] == 2 and out["fence"]["node"] == "DESK"

    # The old authority is fenced before it rejoins, so stale configuration cannot make it write.
    code, out = _run(capsys, "advance", "--db", str(old_authority), "--epoch", "2", "--node", "DESK")
    assert code == 0

    restored = ConversationStore(new_db)
    assert restored.get_message(run_card_id("run-1")) is not None  # IDs survive the move


@pytest.mark.unit
def test_advance_refuses_to_move_backwards(old_authority: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _run(capsys, "advance", "--db", str(old_authority), "--epoch", "3", "--node", "CT")
    code, out = _run(capsys, "advance", "--db", str(old_authority), "--epoch", "2", "--node", "CT")
    assert code == 1 and "below the fence" in out["error"]
    code, out = _run(capsys, "advance", "--db", str(old_authority), "--epoch", "3", "--node", "DESK")
    assert code == 1 and "node change" in out["error"]


@pytest.mark.unit
def test_env_prints_the_settings_for_every_node(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(capsys, "env", "--node", "DESK", "--url", "http://desk:8321/", "--epoch", "2")
    assert code == 0
    assert out["env"] == {
        "BARB_AUTHORITY_NODE": "DESK",
        "BARB_AUTHORITY_URL": "http://desk:8321",
        "BARB_AUTHORITY_EPOCH": "2",
        "FLEET_API_URL": "http://desk:8321",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "argv",
    [
        ("status", "--db", "missing.sqlite3"),
        ("env", "--node", "DESK", "--url", "ftp://desk", "--epoch", "2"),
        ("env", "--node", "DESK", "--url", "http://desk:8321", "--epoch", "0"),
    ],
)
def test_usage_errors_exit_2(argv: tuple[str, ...], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = [a if a != "missing.sqlite3" else str(tmp_path / a) for a in argv]
    code, out = _run(capsys, *args)
    assert code == 2 and out["error"]
