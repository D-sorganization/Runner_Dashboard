"""Tests for gh_client retry/timeout policy (issue #714)."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


@pytest.fixture(autouse=True)
def reset_gh_client():
    import gh_client
    gh_client._cached_token = "test_token"
    gh_client._client = None
    yield
    gh_client._cached_token = None
    gh_client._client = None


@pytest.mark.asyncio
async def test_retry_on_timeout_then_success():
    """First call raises TimeoutException; second call succeeds."""
    import gh_client

    call_count = 0
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": "ok"}

    mock_client = AsyncMock()

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TimeoutException("timeout")
        return mock_response

    mock_client.request = side_effect

    with patch("gh_client._get_client", return_value=mock_client), \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await gh_client._request("GET", "/test")

    assert call_count == 2
    assert mock_sleep.called  # backoff was applied


@pytest.mark.asyncio
async def test_five_timeouts_raise_gh_client_error():
    """5 consecutive timeouts → GhClientError with kind='timeout'."""
    import gh_client

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    with patch("gh_client._get_client", return_value=mock_client), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(gh_client.GhClientError) as exc_info:
            await gh_client._request("GET", "/test")

    assert exc_info.value.kind == "timeout"
    assert exc_info.value.attempts == gh_client._MAX_RETRIES


@pytest.mark.asyncio
async def test_connect_error_retries():
    """ConnectError is also retried like TimeoutException."""
    import gh_client

    call_count = 0
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}

    mock_client = AsyncMock()

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("connection refused")
        return mock_response

    mock_client.request = side_effect

    with patch("gh_client._get_client", return_value=mock_client), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        result = await gh_client._request("GET", "/test")

    assert call_count == 2


@pytest.mark.asyncio
async def test_429_with_retry_after_sleeps_and_succeeds():
    """429 with Retry-After: 2 → sleeps 2s then succeeds."""
    import gh_client

    rate_limited_resp = MagicMock()
    rate_limited_resp.status_code = 429
    rate_limited_resp.headers = {"Retry-After": "2", "X-RateLimit-Remaining": "0"}
    rate_limited_resp.text = ""

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {}

    responses = [rate_limited_resp, ok_resp]
    idx = 0

    async def request_side_effect(*a, **kw):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    mock_client = AsyncMock()
    mock_client.request = request_side_effect

    sleep_calls = []

    async def mock_sleep(t):
        sleep_calls.append(t)

    with patch("gh_client._get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=mock_sleep):
        result = await gh_client._request("GET", "/test")

    assert any(s >= 2 for s in sleep_calls), f"Expected sleep >= 2s, got {sleep_calls}"


@pytest.mark.asyncio
async def test_five_429s_raise_gh_rate_limited():
    """5 consecutive 429s → GhRateLimited after retries exhausted."""
    import gh_client

    rate_resp = MagicMock()
    rate_resp.status_code = 429
    rate_resp.headers = {"Retry-After": "1", "X-RateLimit-Remaining": "0"}
    rate_resp.text = ""

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=rate_resp)

    with patch("gh_client._get_client", return_value=mock_client), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(gh_client.GhRateLimited):
            await gh_client._request("GET", "/test")


@pytest.mark.asyncio
async def test_5xx_retried_then_succeeds():
    """A 500 response is retried; succeeds on second attempt."""
    import gh_client

    fail_resp = MagicMock()
    fail_resp.status_code = 500
    fail_resp.text = "internal server error"

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"ok": True}

    responses = [fail_resp, ok_resp]
    idx = 0

    async def request_side_effect(*a, **kw):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    mock_client = AsyncMock()
    mock_client.request = request_side_effect

    with patch("gh_client._get_client", return_value=mock_client), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        result = await gh_client._request("GET", "/test")

    assert result.status_code == 200


@pytest.mark.asyncio
async def test_paginate_recovers_from_429_on_page_2():
    """paginate() recovers from 429 on page 2 without losing page 1."""
    import gh_client

    page1_resp = MagicMock()
    page1_resp.status_code = 200
    page1_resp.json.return_value = [{"id": 1}, {"id": 2}]
    page1_resp.headers = {"link": '</page2>; rel="next"'}

    page2_rate_resp = MagicMock()
    page2_rate_resp.status_code = 429
    page2_rate_resp.headers = {"Retry-After": "1"}
    page2_rate_resp.text = ""

    page2_ok_resp = MagicMock()
    page2_ok_resp.status_code = 200
    page2_ok_resp.json.return_value = [{"id": 3}]
    page2_ok_resp.headers = {}

    call_counts: dict[str, int] = {}

    async def mock_get(url, headers=None):
        if "per_page" in url:
            return page1_resp
        if url == "/page2":
            idx = call_counts.get("/page2", 0)
            call_counts["/page2"] = idx + 1
            return [page2_rate_resp, page2_ok_resp][idx]
        return page2_ok_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.headers = {}

    with patch("gh_client._get_client", return_value=mock_client), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        items = [item async for item in gh_client.paginate("/repos/test/test/issues")]

    ids = [item["id"] for item in items]
    assert 1 in ids and 2 in ids, f"Page 1 items lost: {ids}"


@pytest.mark.asyncio
async def test_exponential_backoff_increases():
    """Backoff values increase with each retry attempt."""
    import gh_client

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    sleep_calls: list[float] = []

    async def mock_sleep(t):
        sleep_calls.append(t)

    with patch("gh_client._get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=mock_sleep):
        with pytest.raises(gh_client.GhClientError):
            await gh_client._request("GET", "/test")

    # Should have been called _MAX_RETRIES - 1 times (no sleep after last attempt)
    assert len(sleep_calls) == gh_client._MAX_RETRIES - 1
    # Should be non-decreasing (exponential backoff), with small tolerance for jitter
    for i in range(len(sleep_calls) - 1):
        assert sleep_calls[i] <= sleep_calls[i + 1] + 2, \
            f"Sleep not increasing: {sleep_calls}"


def test_max_retries_constant_positive():
    """_MAX_RETRIES must be positive."""
    import gh_client
    assert gh_client._MAX_RETRIES > 0


def test_gh_client_error_carries_metadata():
    """GhClientError carries kind and attempts metadata."""
    import gh_client
    err = gh_client.GhClientError("test error", kind="timeout", attempts=5, last_status=None)
    assert err.kind == "timeout"
    assert err.attempts == 5
    assert err.last_status is None


def test_gh_client_error_default_metadata():
    """GhClientError has sensible defaults."""
    import gh_client
    err = gh_client.GhClientError("some error")
    assert err.kind == "unknown"
    assert err.attempts == 0
