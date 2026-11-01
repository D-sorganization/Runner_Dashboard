"""Unavailable Unix sockets must leave tailscaled identity unverified (#3592)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
import tailnet_identity


def test_missing_unix_socket_family_does_not_attempt_another_transport(monkeypatch) -> None:
    monkeypatch.delattr(tailnet_identity.socket, "AF_UNIX", raising=False)
    make_socket = Mock()
    monkeypatch.setattr(tailnet_identity.socket, "socket", make_socket)
    connection = tailnet_identity._UnixSocketHTTPConnection("unused-fixture.sock")
    with pytest.raises(OSError, match="Unix sockets are unavailable"):
        connection.connect()
    make_socket.assert_not_called()


def test_missing_unix_socket_family_leaves_whois_unverified(monkeypatch) -> None:
    monkeypatch.delattr(tailnet_identity.socket, "AF_UNIX", raising=False)
    assert tailnet_identity._tailscaled_whois("100.64.0.7", 443) is None


def test_available_unix_socket_family_retains_the_existing_transport(monkeypatch) -> None:
    family = 101
    monkeypatch.setattr(tailnet_identity.socket, "AF_UNIX", family, raising=False)
    sock = Mock()
    make_socket = Mock(return_value=sock)
    monkeypatch.setattr(tailnet_identity.socket, "socket", make_socket)
    connection = tailnet_identity._UnixSocketHTTPConnection("fixture.sock", timeout=1.5)
    connection.connect()
    make_socket.assert_called_once_with(family, tailnet_identity.socket.SOCK_STREAM)
    sock.settimeout.assert_called_once_with(1.5)
    sock.connect.assert_called_once_with("fixture.sock")
    assert connection.sock is sock
