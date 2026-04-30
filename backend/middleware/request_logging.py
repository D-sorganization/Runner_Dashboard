"""Request logging middleware extracted from server.py (issue #299).

Provides:
- Request/response timing and logging
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    pass

log = logging.getLogger("dashboard")


async def log_requests(request: Request, call_next: Any) -> Any:
    """Log HTTP requests with timing (middleware).

    Skips verbose logging for certain endpoints (health checks, system endpoints)
    to avoid polluting logs while maintaining traceability of API calls.

    Args:
        request: Incoming FastAPI request
        call_next: Next middleware/endpoint handler

    Returns:
        Response from next handler
    """
    start = time.time()
    response = await call_next(request)
    elapsed = round((time.time() - start) * 1000, 1)
    skip = (
        "/api/system",
        "/api/repos",
        "/api/reports",
        "/api/heavy-tests",
        "/api/scheduled-workflows",
    )
    if not request.url.path.startswith(skip):
        log.info(
            "%s %s → %s (%sms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
    return response
