import asyncio
import threading
import time

import pytest
from fastapi import HTTPException
from routers import coordination


@pytest.mark.parametrize("endpoint", ["jobs", "briefing", "message", "presence"])
def test_slow_coordination_upstream_returns_retryable_timeout(monkeypatch, endpoint):
    gate = threading.Event()
    monkeypatch.setattr(coordination, "COORDINATION_TIMEOUT", 0.05, raising=False)

    def slow(*args, **kwargs):
        gate.wait(2)
        return {}

    async def slow_brief(*args, **kwargs):
        await asyncio.to_thread(slow)
        return {}

    monkeypatch.setattr(coordination.jobs_mod, "list_jobs", slow)
    monkeypatch.setattr(coordination, "build_briefing", slow_brief)
    monkeypatch.setattr(coordination.service, "send_message", slow)
    monkeypatch.setattr(coordination.service, "register_presence", slow)

    async def call():
        if endpoint == "jobs":
            return await coordination.get_jobs(repo="Tools", limit=5, include_excluded=False, _peer="test")
        if endpoint == "briefing":
            return await coordination.get_briefing(repo="Tools", agent=None, _peer="test")
        if endpoint == "message":
            return await coordination.post_message(body=None, caller=None)
        return await coordination.post_presence(body=None, caller=None)

    async def verify():
        started = time.monotonic()
        try:
            with pytest.raises(HTTPException) as caught:
                await call()
            assert time.monotonic() - started < 1
            assert caught.value.status_code == 503
            assert caught.value.detail["retryable"] is True
            if endpoint in ("message", "presence"):
                assert caught.value.detail["outcome"] == "unknown"
        finally:
            gate.set()

    asyncio.run(verify())
