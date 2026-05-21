"""Help-chat API endpoint.

Extracted from server.py (issue #2942).

Route
-----
POST /api/help/chat — answer a dashboard help question
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_scope  # noqa: B008

from help_chat import faq_lookup as _faq_lookup, tab_fallback_answer as _tab_fallback_answer

log = logging.getLogger("dashboard")

router = APIRouter(tags=["help"])

_default_llm_model: str = "claude-haiku-4-5-20251001"


def set_llm_model(model: str) -> None:
    """Inject the LLM model name (called from server.py at startup)."""
    global _default_llm_model  # noqa: PLW0603
    _default_llm_model = model


@router.post("/api/help/chat")
async def help_chat(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("operator")),  # noqa: B008
) -> dict:
    """Answer a dashboard help question.

    Uses local FAQ first, falls back to Claude API if available.
    """
    body = await request.json()
    question = str(body.get("question", "")).strip()
    current_tab = str(body.get("current_tab", "")).strip()
    if not question:
        raise HTTPException(status_code=422, detail="question required")

    faq_match = _faq_lookup(question, current_tab)
    if faq_match:
        return {"answer": faq_match, "source": "faq"}

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        try:
            system_prompt = (
                "You are a helpful assistant for a GitHub Actions runner dashboard. "
                "The dashboard has these tabs: Fleet, Queue, History, Machines, Organization, "
                "Heavy Tests, Stats, Reports, Scheduled Workflows, Runner Plan, Local Tools, "
                "Deployment, Remediation, Workflows, Credentials, Assessments, Feature Requests, Maxwell. "
                f"The user is currently on the '{current_tab}' tab. "
                "Answer concisely in 1-3 sentences. Focus on how to accomplish tasks in the dashboard."
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": _default_llm_model,
                        "max_tokens": 200,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": question}],
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    answer = data.get("content", [{}])[0].get("text", "")
                    if answer:
                        return {"answer": answer, "source": "claude"}
        except Exception as e:  # noqa: BLE001
            log.warning("help_chat claude fallback failed: %s", e)

    return {"answer": _tab_fallback_answer(current_tab), "source": "fallback"}
