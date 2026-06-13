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


class TestSafeForwardHeaders:
    """Verify that _safe_forward_headers strips sensitive caller headers (issue #347)."""

    def _make_request_with_headers(self, headers: dict[str, str]) -> MagicMock:
        mock = MagicMock()
        mock.headers.items.return_value = list(headers.items())
        return mock

    def test_authorization_header_stripped(self) -> None:
        req = self._make_request_with_headers(
            {"Authorization": "Bearer caller-token", "X-Requested-With": "XMLHttpRequest"}
        )
        result = proxy_utils._safe_forward_headers(req)
        assert "Authorization" not in result
        # X-Requested-With is safe and should pass through
        assert result.get("X-Requested-With") == "XMLHttpRequest"

    def test_cookie_header_stripped(self) -> None:
        req = self._make_request_with_headers({"Cookie": "session=abc123", "Accept": "application/json"})
        result = proxy_utils._safe_forward_headers(req)
        assert "Cookie" not in result

    def test_x_api_key_stripped(self) -> None:
        req = self._make_request_with_headers({"X-API-Key": "secret-key", "Content-Type": "application/json"})
        result = proxy_utils._safe_forward_headers(req)
        assert "X-API-Key" not in result

    def test_x_csrf_token_stripped(self) -> None:
        req = self._make_request_with_headers({"X-CSRF-Token": "csrf-value"})
        result = proxy_utils._safe_forward_headers(req)
        assert "X-CSRF-Token" not in result

    def test_hub_fleet_token_injected_when_set(self, monkeypatch) -> None:
        monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret-token")
        req = self._make_request_with_headers({"X-Requested-With": "XMLHttpRequest"})
        result = proxy_utils._safe_forward_headers(req)
        assert result.get("Authorization") == "Bearer fleet-secret-token"

    def test_hub_fleet_token_not_injected_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
        req = self._make_request_with_headers({"X-Requested-With": "XMLHttpRequest"})
        result = proxy_utils._safe_forward_headers(req)
        assert "Authorization" not in result

    def test_host_and_content_length_stripped(self) -> None:
        req = self._make_request_with_headers(
            {"host": "localhost:8321", "content-length": "42", "Accept": "application/json"}
        )
        result = proxy_utils._safe_forward_headers(req)
        assert "host" not in result
        assert "content-length" not in result
        assert result.get("Accept") == "application/json"

    def test_case_insensitive_header_stripping(self) -> None:
        req = self._make_request_with_headers(
            {"AUTHORIZATION": "Bearer caller-token", "COOKIE": "s=x", "X-API-KEY": "secret"}
        )
        result = proxy_utils._safe_forward_headers(req)
        assert not any(k.upper() in {"AUTHORIZATION", "COOKIE", "X-API-KEY"} for k in result)


class TestHubCircuitBreaker:
    """The hub circuit breaker makes a node serve local data when the hub is down."""

    def teardown_method(self) -> None:
        # Never leak open-breaker state into other tests.
        proxy_utils.reset_hub_circuit()

    def test_mark_then_in_cooldown(self) -> None:
        proxy_utils.reset_hub_circuit()
        assert proxy_utils.hub_in_cooldown() is False
        proxy_utils.mark_hub_unreachable()
        assert proxy_utils.hub_in_cooldown() is True

    def test_reset_closes_breaker(self) -> None:
        proxy_utils.mark_hub_unreachable()
        proxy_utils.reset_hub_circuit()
        assert proxy_utils.hub_in_cooldown() is False

    def test_expired_cooldown_is_closed(self) -> None:
        # A non-positive cooldown lands in the past, so the breaker is already closed.
        proxy_utils.mark_hub_unreachable(cooldown_s=-1.0)
        assert proxy_utils.hub_in_cooldown() is False

    def test_open_breaker_forces_local_even_for_node_with_hub(self) -> None:
        # Normally a node with a hub and no local/scope params proxies (returns True);
        # with the breaker open it must serve local (False) instead.
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is True
                proxy_utils.mark_hub_unreachable()
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_open_breaker_marks_degraded_for_node_hub_fallback(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                proxy_utils.mark_hub_unreachable()
                assert proxy_utils.should_mark_hub_circuit_degraded(_make_request({})) is True

    def test_explicit_local_request_is_not_hub_circuit_degraded(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                proxy_utils.mark_hub_unreachable()
                assert proxy_utils.should_mark_hub_circuit_degraded(_make_request({"local": "true"})) is False
                assert proxy_utils.should_mark_hub_circuit_degraded(_make_request({"scope": "local"})) is False

    def test_non_node_or_missing_hub_is_not_hub_circuit_degraded(self) -> None:
        proxy_utils.mark_hub_unreachable()
        with patch.object(proxy_utils, "MACHINE_ROLE", "hub"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                assert proxy_utils.should_mark_hub_circuit_degraded(_make_request({})) is False
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", ""):
                assert proxy_utils.should_mark_hub_circuit_degraded(_make_request({})) is False
