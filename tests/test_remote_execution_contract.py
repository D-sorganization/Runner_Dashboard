"""Unit tests for remote_execution_contract.py — private-host detection and envelope validation."""

from remote_execution_contract import (  # noqa: E402
    RemoteExecutionEnvelope,
    _host_is_private,
    _url_is_private,
    validate_envelope,
)

# ---------------------------------------------------------------------------
# _host_is_private
# ---------------------------------------------------------------------------


def test_host_is_private_localhost() -> None:
    assert _host_is_private("localhost") is True


def test_host_is_private_192_168() -> None:
    assert _host_is_private("192.168.1.1") is True


def test_host_is_private_10_network() -> None:
    assert _host_is_private("10.0.0.1") is True


def test_host_is_private_public_dns() -> None:
    assert _host_is_private("8.8.8.8") is False


# ---------------------------------------------------------------------------
# _url_is_private
# ---------------------------------------------------------------------------


def test_url_is_private_lan_ip() -> None:
    assert _url_is_private("http://192.168.1.10:8321") is True


def test_url_is_private_github_com() -> None:
    assert _url_is_private("https://github.com") is False


# ---------------------------------------------------------------------------
# validate_envelope — unknown target (no registry) → not accepted
# ---------------------------------------------------------------------------


def _make_envelope_with_target(target: str) -> RemoteExecutionEnvelope:
    return RemoteExecutionEnvelope(
        action="node.status",
        source="hub",
        target=target,
        requested_by="alice",
    )


def test_validate_envelope_unknown_target_no_registry_rejected() -> None:
    env = _make_envelope_with_target("completely-unknown-machine-xyz")
    result = validate_envelope(env, registry=None)
    assert result.accepted is False
    assert "inventory" in result.reason.lower() or "not" in result.reason.lower()
