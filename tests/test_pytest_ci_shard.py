"""Contract tests for merge_group pytest sharding helper."""

from __future__ import annotations

import pytest

from scripts.pytest_ci_shard import (
    _collect_node_ids,
    _parse_collected_node_ids,
    pytest_collection_modifyitems,
    pytest_runtest_teardown,
    select_shard,
)


def test_parse_collected_node_ids_ignores_warnings_and_summaries() -> None:
    sample_stdout = """
tests/a.py::test_one
tests/b.py::TestSuite::test_two[param with space]
tests/c.py: 12

=============================== warnings summary ===============================
backend/main.py:45
  /path/backend/main.py:45: DeprecationWarning: on_event is deprecated
    app.on_event("startup")
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
2/10 tests collected (8 deselected) in 0.12s
"""
    assert _parse_collected_node_ids(sample_stdout) == [
        "tests/a.py::test_one",
        "tests/b.py::TestSuite::test_two[param with space]",
    ]


def test_collect_node_ids_overrides_addopts_to_single_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    captured_cmd: list[str] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_cmd.extend(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="tests/a.py::test_one\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _collect_node_ids("not slow") == ["tests/a.py::test_one"]
    assert "-o" in captured_cmd
    assert "addopts=-q" in captured_cmd


def test_select_shard_splits_evenly() -> None:
    nodes = [f"tests/t_{i}.py::test_x" for i in range(6)]
    assert select_shard(nodes, 2, 1) == nodes[0::2]
    assert select_shard(nodes, 2, 2) == nodes[1::2]


def test_select_shard_single_group_returns_all() -> None:
    nodes = ["tests/a.py::test_one", "tests/b.py::test_two"]
    assert select_shard(nodes, 1, 1) == nodes


def test_select_shard_rejects_invalid_index() -> None:
    with pytest.raises(ValueError, match="index"):
        select_shard([], 2, 0)
    with pytest.raises(ValueError, match="index"):
        select_shard([], 2, 3)


def test_collection_modifyitems_shards_in_process_and_reports_deselected() -> None:
    from unittest.mock import MagicMock

    items = [MagicMock(name=f"item_{i}") for i in range(4)]
    deselected_calls: list[list[object]] = []
    config = MagicMock()
    opts = {"--ci-shard-groups": 2, "--ci-shard-index": 2}
    config.getoption.side_effect = lambda name, default=None: opts.get(name, default)
    config.hook.pytest_deselected.side_effect = lambda items: deselected_calls.append(list(items))

    working = list(items)
    pytest_collection_modifyitems(config, working)
    assert working == [items[1], items[3]]
    assert deselected_calls == [[items[0], items[2]]]


def test_collection_modifyitems_noop_when_single_shard() -> None:
    from unittest.mock import MagicMock

    items = [MagicMock(name="item_0"), MagicMock(name="item_1")]
    config = MagicMock()
    config.getoption.return_value = 1

    working = list(items)
    pytest_collection_modifyitems(config, working)
    assert working == items
    config.hook.pytest_deselected.assert_not_called()


def test_ci_standard_tests_matrix_two_shards_on_merge_group_only() -> None:
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci-standard.yml").read_text(encoding="utf-8")
    )
    include = workflow["jobs"]["tests"]["strategy"]["matrix"]["include"]
    assert isinstance(include, str), "tests matrix must use a dynamic include expression"
    compact = include.replace(" ", "")
    assert "merge_group" in compact
    assert '"shards":2' in compact
    assert '"shard":1' in compact and '"shard":2' in compact
    assert '"shards":1' in compact


def test_tests_job_does_not_wait_on_lint() -> None:
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci-standard.yml").read_text(encoding="utf-8")
    )
    needs = workflow["jobs"]["tests"]["needs"]
    assert "lint" not in needs
    assert "ci-health-check" in needs


def test_runtest_teardown_resets_runner_control_health_when_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types
    from unittest.mock import MagicMock

    reset_mock = MagicMock()
    fake_mod = types.SimpleNamespace(reset_runner_control_health=reset_mock)
    monkeypatch.setitem(sys.modules, "runner_control_health", fake_mod)

    pytest_runtest_teardown(MagicMock())
    reset_mock.assert_called_once_with()
