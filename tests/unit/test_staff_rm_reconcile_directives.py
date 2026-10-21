"""Directive reconcile from Repository_Management staff/directives.yml."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from priorities.directives import DirectivesList, Directive
from staff.rm_directives import (
    DirectivesReconcileError,
    load_directives_yaml,
    reconcile_directives,
    reconcile_directives_for_rm_root,
)


@pytest.mark.unit
def test_reconcile_adds_yaml_directive(tmp_path: Path) -> None:
    path = tmp_path / "directives.json"
    dl = DirectivesList(path)
    diff = reconcile_directives(
        [{"text": "ship OAuth", "set_by": "owner", "repo": "Runner_Dashboard", "priority": 1}],
        "abc",
        directives=dl,
    )
    assert diff.added == ["ship OAuth"]
    rows = dl.load()
    assert len(rows) == 1 and rows[0].managed and rows[0].active


@pytest.mark.unit
def test_reconcile_deactivates_managed_removed_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "directives.json"
    dl = DirectivesList(path)
    dl.replace_all(
        [
            Directive(
                id="dir-abc",
                text="old focus",
                set_by="owner",
                managed=True,
                active=True,
            )
        ]
    )
    diff = reconcile_directives([], "sha", directives=dl)
    assert diff.deactivated == ["old focus"]
    assert dl.load()[0].active is False


@pytest.mark.unit
def test_reconcile_leaves_operator_directives(tmp_path: Path) -> None:
    path = tmp_path / "directives.json"
    dl = DirectivesList(path)
    dl.replace_all(
        [
            Directive(id="dir-op", text="api added", set_by="principal:op", managed=False, active=True),
        ]
    )
    reconcile_directives([{"text": "from yaml", "set_by": "owner"}], "sha", directives=dl)
    texts = {d.text for d in dl.load()}
    assert texts == {"api added", "from yaml"}


@pytest.mark.unit
def test_expired_yaml_directive_stored_inactive(tmp_path: Path) -> None:
    path = tmp_path / "directives.json"
    dl = DirectivesList(path)
    past = datetime(2020, 1, 1, tzinfo=UTC)
    reconcile_directives(
        [{"text": "late", "set_by": "owner", "expires": "2019-01-01T00:00:00Z"}],
        "sha",
        directives=dl,
        now=past,
    )
    assert dl.load()[0].active is False


@pytest.mark.unit
def test_load_directives_yaml_parse_failure(tmp_path: Path) -> None:
    bad = tmp_path / "directives.yml"
    bad.write_text("directives: [unclosed\n", encoding="utf-8")
    with pytest.raises(DirectivesReconcileError):
        load_directives_yaml(bad)


@pytest.mark.unit
def test_reconcile_for_rm_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "rm"
    (root / "staff").mkdir(parents=True)
    (root / "staff" / "directives.yml").write_text(
        "directives:\n  - text: fleet focus\n    set_by: owner\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "out.json"))
    diff = reconcile_directives_for_rm_root(root, "commit1", dry_run=False)
    assert diff.added == ["fleet focus"]
