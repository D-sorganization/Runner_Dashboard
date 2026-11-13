"""Read-only request boundary for the local sample-data demo."""

from __future__ import annotations

from starlette.responses import JSONResponse


def demo_api_block(path: str, *, enabled: bool) -> JSONResponse | None:
    """Reject API access while demo mode is enabled, keeping the view offline."""
    if enabled and path.startswith("/api/"):
        return JSONResponse(
            {"detail": "Demo mode uses local sample data; API access is disabled."},
            status_code=403,
        )
    return None
