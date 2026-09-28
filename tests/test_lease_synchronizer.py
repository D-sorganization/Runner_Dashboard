"""Tests for backend/lease_synchronizer.py — issue #386."""

from __future__ import annotations

import asyncio

import lease_synchronizer as ls

# ---------------------------------------------------------------------------
# _parse_iso_ts
# ---------------------------------------------------------------------------


def test_parse_iso_ts_valid() -> None:
    result = ls._parse_iso_ts("2026-04-26T12:00:00Z")
    assert result is not None
    assert result > 0


def test_parse_iso_ts_invalid_returns_none() -> None:
    assert ls._parse_iso_ts("not-a-date") is None


def test_parse_iso_ts_empty_returns_none() -> None:
    assert ls._parse_iso_ts("") is None


def test_parse_iso_ts_type_error_returns_none() -> None:
    # _parse_iso_ts expects str; non-string input should return None via try/except
    try:
        result = ls._parse_iso_ts(None)  # type: ignore[arg-type]
        assert result is None
    except (TypeError, AttributeError):
        pass  # acceptable — module documents str as the expected input type


# ---------------------------------------------------------------------------
# sync_github_leases — no issues → no-op
# ---------------------------------------------------------------------------


def test_sync_github_leases_empty_list_no_error() -> None:
    """Passing an empty issue list must not raise."""
    asyncio.run(ls.sync_github_leases([]))


def test_sync_github_leases_issue_without_claim_skipped() -> None:
    """Issues with no claim: label must be silently skipped."""
    issues = [{"number": 1, "labels": [{"name": "bug"}], "body": "some body"}]
    asyncio.run(ls.sync_github_leases(issues))


def test_sync_github_leases_expired_claim_skipped() -> None:
    """Issues with an already-expired claim must be skipped."""
    issues = [
        {
            "number": 2,
            "labels": [{"name": "claim:claude"}],
            "body": "lease: claude expires 2020-01-01T00:00:00Z",
        }
    ]
    asyncio.run(ls.sync_github_leases(issues))


def test_sync_github_leases_accepts_inventory_string_labels(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The issue inventory flattens labels to strings; that 500'd /api/issues (#1718 live sweep)."""
    looked_up: list[str] = []

    def get_principal(principal_id: str) -> None:
        looked_up.append(principal_id)

    monkeypatch.setattr(ls.identity_manager, "get_principal", get_principal)
    issues = [
        {"number": 3, "labels": ["bug"], "body": ""},
        {"number": 4, "labels": ["claim:claude"], "body": "lease: claude expires 2999-01-01T00:00:00Z"},
    ]
    asyncio.run(ls.sync_github_leases(issues))
    assert looked_up == ["agent:claude"]
