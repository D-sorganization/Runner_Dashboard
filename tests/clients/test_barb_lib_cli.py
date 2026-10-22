"""Barb lib subcommands (#2165 slice 8)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_FLEET = Path(__file__).resolve().parents[2] / "clients" / "fleet"
if str(_FLEET) not in sys.path:
    sys.path.insert(0, str(_FLEET))

import barb  # noqa: E402
from barb_lib import cmd_lib_search, cmd_lib_submit  # noqa: E402


def test_lib_search_calls_library_api() -> None:
    client = mock.Mock()
    client.library_search.return_value = {"passages": []}
    ns = type("NS", (), {"query": "hip torque", "repo": "UpstreamDrift", "topic": "", "budget": 1200, "k": 0})()
    with mock.patch("builtins.print") as printed:
        code = cmd_lib_search(client, ns)
    assert code == 0
    client.library_search.assert_called_once_with(
        "hip torque", repo="UpstreamDrift", topic=None, budget=1200, k=None
    )
    printed.assert_called_once()


def test_lib_submit_notifies_alexandria() -> None:
    client = mock.Mock()
    client.library_submit.return_value = {"id": 9, "status": "pending"}
    ns = type(
        "NS",
        (),
        {
            "doi": "10.5555/xyz",
            "url": "",
            "isbn": "",
            "title": "Book",
            "authors": "Ada",
            "reason": "needed",
            "requesting_ref": "UpstreamDrift#42",
            "excerpt": "",
            "notify": True,
        },
    )()
    with mock.patch("builtins.print"):
        code = cmd_lib_submit(client, ns)
    assert code == 0
    client.library_submit.assert_called_once()
    client.agent_bus_send.assert_called_once()
    call = client.agent_bus_send.call_args.kwargs
    assert call["to"] == "alexandria"
    assert call["repo"] == "UpstreamDrift"
    assert call["work_number"] == 42
