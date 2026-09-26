"""Tests never proxy to a live hub, even on a fleet node (#1577).

A node's environment has ``MACHINE_ROLE=node`` and ``HUB_URL``. ``proxy_utils``
binds both at import, so without the autouse ``_no_hub_proxy`` fixture fleet
endpoints forwarded test requests to the real hub. Run this file with
``MACHINE_ROLE=node HUB_URL=http://127.0.0.1:9`` to see it fail without the fixture.
"""

from __future__ import annotations

import proxy_utils
import pytest
from starlette.requests import Request


def _request(path: str = "/api/runners") -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


@pytest.mark.unit
def test_hub_url_is_cleared_for_every_test() -> None:
    assert proxy_utils.HUB_URL is None


@pytest.mark.unit
def test_a_node_role_does_not_proxy_fleet_requests_inside_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_utils, "MACHINE_ROLE", "node")

    assert proxy_utils.should_proxy_fleet_to_hub(_request()) is False
