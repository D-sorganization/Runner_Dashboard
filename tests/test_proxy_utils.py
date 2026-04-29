"""Unit tests for backend/proxy_utils.py (issue #155)."""

from __future__ import annotations

import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import proxy_utils  # noqa: E402


def _make_request(query_params: dict[str, str]) -> MagicMock:
    """Return a mocked FastAPI Request with the given query_params."""
    mock = MagicMock()
    mock.query_params.get = lambda key, default="": query_params.get(key, default)
    return mock


class TestShouldProxyFleetToHub:
    def test_not_node_role_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "hub"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_no_hub_url_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", None):
                req = _make_request({})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_node_with_hub_no_local_params_returns_true(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is True

    def test_local_param_true_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "true"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_local_param_yes_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "yes"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_local_param_1_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "1"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_local_param_local_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "local"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_local_param_false_returns_true(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "false"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is True

    def test_local_param_0_returns_true(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "0"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is True

    def test_scope_local_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"scope": "local"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_scope_fleet_returns_true(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"scope": "fleet"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is True

    def test_local_case_insensitive(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "TRUE"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_scope_case_insensitive(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"scope": "LOCAL"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_multiple_params_local_wins(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "true", "scope": "fleet"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_multiple_params_scope_wins(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "false", "scope": "local"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_empty_hub_url_string_not_configured(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", ""):
                req = _make_request({})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False
