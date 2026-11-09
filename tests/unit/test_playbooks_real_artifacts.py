"""Reviewed Barb Operating Manual + seed playbook artifacts under docs/playbooks/ (#3487).

CI validates every checked-in playbook file against the #3486 schema and
anchor/DRY linter so a broken real artifact fails the build, not just a
test fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.playbooks import (
    BarbOperatingManual,
    PlaybookDefinition,
    assert_playbooks_clean,
    parse_operating_manual,
    parse_playbook,
)

DOCS_PLAYBOOKS_DIR = Path(__file__).resolve().parents[2] / "docs" / "playbooks"


def _manual_path() -> Path:
    for name in ("manual.yml", "manual.yaml"):
        p = DOCS_PLAYBOOKS_DIR / name
        if p.is_file():
            return p
    raise AssertionError(f"no manual.yml/.yaml found under {DOCS_PLAYBOOKS_DIR}")


def _playbook_paths() -> list[Path]:
    manual_names = {"manual.yml", "manual.yaml"}
    return sorted(
        p for p in (*DOCS_PLAYBOOKS_DIR.glob("*.yml"), *DOCS_PLAYBOOKS_DIR.glob("*.yaml")) if p.name not in manual_names
    )


def _load_manual() -> BarbOperatingManual:
    return parse_operating_manual(_manual_path().read_text(encoding="utf-8"))


def _load_playbooks() -> list[PlaybookDefinition]:
    return [parse_playbook(p.read_text(encoding="utf-8")) for p in _playbook_paths()]


def test_docs_playbooks_dir_has_a_manual_and_at_least_two_seed_playbooks() -> None:
    assert DOCS_PLAYBOOKS_DIR.is_dir()
    assert _manual_path().is_file()
    assert len(_playbook_paths()) >= 2


def test_real_manual_parses_and_covers_the_expected_policy_anchors() -> None:
    manual = _load_manual()
    anchors = {section.anchor for section in manual.sections}
    assert {"claim-before-touching", "no-holds-bypass", "identity-bot-only"} <= anchors


@pytest.mark.parametrize("path", _playbook_paths(), ids=lambda p: p.stem)
def test_each_real_playbook_file_parses(path: Path) -> None:
    parse_playbook(path.read_text(encoding="utf-8"))


def test_real_playbooks_pass_the_anchor_and_dry_linter_against_the_real_manual() -> None:
    manual = _load_manual()
    playbooks = _load_playbooks()
    assert_playbooks_clean(playbooks, manual)


def test_store_resolves_docs_playbooks_as_a_local_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import staff.playbooks.store as store_mod

    monkeypatch.delenv(store_mod.PLAYBOOKS_DIR_ENV, raising=False)
    store_mod.clear_playbooks_cache()
    try:
        resolved = store_mod.playbooks_dir()
        # A sibling Repository_Management checkout, if present on this host,
        # legitimately outranks the local fallback -- only assert the
        # fallback itself resolves correctly when nothing else is found.
        if resolved is not None and resolved.name == "playbooks" and resolved.parent.name == "staff":
            pytest.skip("a sibling Repository_Management/staff/playbooks checkout outranks the local fallback here")
        assert resolved == DOCS_PLAYBOOKS_DIR
        errors = store_mod.playbook_load_errors()
        assert errors == {}
        assert store_mod.load_manual() is not None
        assert set(store_mod.load_playbooks()) >= {"overnight-codex-swarm", "dots-barb-swarm-manager-brief"}
    finally:
        store_mod.clear_playbooks_cache()
