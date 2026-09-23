"""Operator directives: validation, expiry and persistence (issue #1227)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from priorities.directives import Directive, DirectivesList, directives_path

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@pytest.mark.unit
def test_from_dict_defaults_and_stable_id() -> None:
    d = Directive.from_dict({"text": "  Ship the coordination API  ", "set_by": "dieter"})
    assert d.text == "Ship the coordination API"
    assert d.repo == "*"
    assert d.priority == 3
    assert d.id.startswith("dir-")
    assert d.id == Directive.from_dict({"text": "Ship the coordination API", "set_by": "x"}).id
    assert d.expires == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    [
        {"text": "", "set_by": "a"},
        {"text": "x" * 501, "set_by": "a"},
        {"text": "ok", "set_by": ""},
        {"text": "ok", "set_by": "a", "priority": 0},
        {"text": "ok", "set_by": "a", "priority": 6},
        {"text": "ok", "set_by": "a", "priority": True},
        {"text": "ok", "set_by": "a", "priority": "2"},
        {"text": "ok", "set_by": "a", "repo": "owner/repo"},
        {"text": "ok", "set_by": "a", "repo": "../x"},
        {"text": "ok", "set_by": "a", "expires": "next tuesday"},
    ],
)
def test_from_dict_rejects_bad_input(data: dict) -> None:
    with pytest.raises(ValueError):
        Directive.from_dict(data)


@pytest.mark.unit
def test_expires_normalised_to_utc_z() -> None:
    d = Directive.from_dict({"text": "t", "set_by": "a", "expires": "2026-09-23T05:00:00-07:00"})
    assert d.expires == "2026-09-23T12:00:00Z"
    assert d.is_active(NOW)
    assert not d.is_active(datetime(2026, 9, 23, 12, 0, 1, tzinfo=UTC))


@pytest.mark.unit
def test_active_filters_expired_and_sorts_by_priority(tmp_path: Path) -> None:
    store = DirectivesList(tmp_path / "d.json")
    store.replace(
        [
            {"text": "low", "priority": 5, "set_by": "a"},
            {"text": "gone", "priority": 1, "set_by": "a", "expires": _iso(NOW - timedelta(hours=1))},
            {"text": "high", "priority": 1, "set_by": "a", "expires": _iso(NOW + timedelta(days=1))},
        ]
    )
    assert [d.text for d in store.active(NOW)] == ["high", "low"]
    assert len(store.load()) == 3


@pytest.mark.unit
def test_replace_rejects_duplicate_ids(tmp_path: Path) -> None:
    store = DirectivesList(tmp_path / "d.json")
    with pytest.raises(ValueError, match="duplicate"):
        store.replace([{"text": "same", "set_by": "a"}, {"text": "same", "set_by": "b"}])
    assert not (tmp_path / "d.json").exists()


@pytest.mark.unit
def test_missing_or_corrupt_file_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "d.json"
    store = DirectivesList(path)
    assert store.load() == []
    path.write_text("{not json", encoding="utf-8")
    assert store.load() == []


@pytest.mark.unit
def test_file_round_trip_shape(tmp_path: Path) -> None:
    path = tmp_path / "d.json"
    DirectivesList(path).replace([{"text": "focus infra", "repo": "Runner_Dashboard", "set_by": "a"}])
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["directives"][0]["repo"] == "Runner_Dashboard"
    assert "updated_at" in raw


@pytest.mark.unit
def test_env_override_for_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "custom.json"))
    assert directives_path() == tmp_path / "custom.json"
