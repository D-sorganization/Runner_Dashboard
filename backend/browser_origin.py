"""Classify external browser ingress without changing tailnet OAuth origins."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.requests import HTTPConnection

_EDGE_HEADERS = ("cf-ray", "cf-connecting-ip", "cf-access-jwt-assertion", "cf-visitor")


def browser_origin() -> str | None:
    """Return an explicit HTTPS browser origin, or None for invalid/unset config.

    OAuth's DASHBOARD_PUBLIC_ORIGIN may be a private MagicDNS hostname and
    deliberately does not enable the external browser plane.
    """
    value = os.environ.get("DASHBOARD_BROWSER_PUBLIC_ORIGIN", "")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "\\" in value
            or any(char.isspace() for char in value)
        ):
            return None
        _ = parsed.port  # Validate malformed and out-of-range ports.
    except ValueError:
        return None
    return value


def external_browser_request(request: HTTPConnection) -> bool:
    """Select strict handling by edge marker or explicit host, never identity.

    An untrusted marker may only tighten checks; it cannot authenticate callers.
    The Origin header alone cannot move a local request to another cookie plane.
    """
    if any(request.headers.get(header) for header in _EDGE_HEADERS):
        return True
    origin = browser_origin()
    return bool(origin and urlsplit(origin).netloc.casefold() == request.headers.get("host", "").casefold())
