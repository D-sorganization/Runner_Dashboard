"""Sunset schedule for legacy HTTP 410 routes.

Dieter approved a 30-day window starting 2026-10-24. After
``RETIRED_ROUTE_REMOVAL_AFTER``, delete the route handlers (``remediation_retired``,
``assistant`` chat 410 stub) and any remaining OpenAPI path entries.
"""

from __future__ import annotations

from datetime import date

# Last calendar day the 410 stubs remain mounted (inclusive).
RETIRED_ROUTE_REMOVAL_AFTER = date(2026, 11, 23)

# HTTP ``Sunset`` header value (RFC 8594) for retired endpoints during the window.
RETIRED_ROUTE_HTTP_SUNSET = "Sun, 23 Nov 2026 00:00:00 GMT"
