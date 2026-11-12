"""Vendored mirror of enqueue in-flight hold coverage (#4315).

When porting to Repository_Management, copy this file into that repo's test
tree and ensure ``scripts/automerge_guard.py`` matches ``vendor/rm-sync-4315``.
"""

from __future__ import annotations

from tests.test_automerge_guard import test_in_flight_hold_after_enqueue_succeeds_exits_five_on_arm_cli


def test_in_flight_hold_after_enqueue_succeeds_exits_five_on_arm_cli_vendor_mirror(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    test_in_flight_hold_after_enqueue_succeeds_exits_five_on_arm_cli(monkeypatch, caplog, capsys)
