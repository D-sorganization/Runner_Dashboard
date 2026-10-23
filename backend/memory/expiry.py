"""Shared instant comparison for persisted memory expiration timestamps."""

from datetime import datetime


def expiry_is_future(expires_at: str | None, now_iso: str) -> bool:
    """Reject invalid or naive explicit expiries; preserve legacy unbounded rows."""
    if expires_at is None:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at)
        now = datetime.fromisoformat(now_iso)
        return expiry.utcoffset() is not None and now.utcoffset() is not None and expiry > now
    except (TypeError, ValueError, OverflowError):
        return False
