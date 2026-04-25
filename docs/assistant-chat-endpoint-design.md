# Assistant Chat Endpoint Design — Issue #88

**Status:** Design Phase (awaiting maintainer relabel from `judgement:design`)  
**Date:** 2026-04-25

---

## Problem Statement

The dashboard assistant (sidebar) needs a backend endpoint to receive user prompts and return AI-generated responses. Currently, the frontend has an assistant sidebar UI (#87) but no backend integration.

**User Story:**
- Operator types a question in the dashboard sidebar
- Backend receives the prompt with dashboard context (current tab, selected items, etc.)
- Backend calls the configured AI provider (Jules API, Ollama, etc.)
- Response streams back to frontend for display

---

## Architecture Options

### Option A: Simple Prompt Pass-Through
**Approach:** POST /api/assistant/chat → forward prompt to AI provider with no context

**Pros:**
- Minimal backend changes
- Stateless, cacheable responses
- Easy to test

**Cons:**
- AI lacks dashboard context (what tab user is on, what data they're viewing)
- Can't reference selected runners, failed workflows, etc.
- Less useful responses

### Option B: Context-Aware Chat (Recommended)
**Approach:** POST /api/assistant/chat with body containing:
```json
{
  "prompt": "why did this workflow fail?",
  "context": {
    "current_tab": "remediation",
    "selected_run_id": 12345,
    "selected_items": [...],
    "dashboard_state": {...}
  },
  "provider": "jules_api"  // optional override
}
```

**Pros:**
- AI can reference specific dashboard data
- Contextual, actionable responses
- Better UX: "Why did run #12345 fail?" gets specific answer
- Aligns with existing dispatch architecture (context-aware remediation)

**Cons:**
- Larger request payloads
- Need to serialize dashboard state carefully
- Potential security concerns if context leaks secrets

---

## Recommended: Option B (Context-Aware)

Follows existing pattern from `/api/agent-remediation/plan` (#85) which also takes context + prompt.

---

## Implementation Sketch

### Endpoint Definition

```python
@app.post("/api/assistant/chat", tags=["assistant"])
async def assistant_chat(request: Request) -> dict:
    """
    Chat with AI assistant about dashboard state.
    
    Request body:
    {
        "prompt": str,           # User question/request
        "context": {
            "current_tab": str,  # "overview", "remediation", etc.
            "selected_run_id": int (optional),
            "selected_items": list (optional),
            "dashboard_state": dict (optional)
        },
        "provider": str (optional, default from config)
    }
    
    Response:
    {
        "response": str,         # AI response text
        "provider": str,         # Which AI provider was used
        "context_used": dict,    # Echoed context for client verification
        "timestamp": ISO-8601
    }
    """
```

### Request/Response Models

```python
# In backend/dispatch_contract.py (or new file backend/assistant_contract.py)

from pydantic import BaseModel, Field
from typing import Optional, Any

class AssistantContext(BaseModel):
    """Dashboard state context for assistant prompts."""
    current_tab: str = Field(..., description="Active tab: overview, remediation, etc")
    selected_run_id: Optional[int] = None
    selected_items: Optional[list[dict]] = None
    dashboard_state: Optional[dict[str, Any]] = None

class AssistantChatRequest(BaseModel):
    """User prompt + dashboard context for AI assistant."""
    prompt: str = Field(..., min_length=1, max_length=5000)
    context: AssistantContext
    provider: Optional[str] = None  # Override default provider

class AssistantChatResponse(BaseModel):
    """AI assistant response."""
    response: str
    provider: str
    context_used: dict
    timestamp: str  # ISO-8601
```

### Backend Implementation

```python
async def assistant_chat(request: Request) -> dict:
    try:
        payload = await request.json()
    except JSONDecodeError:
        return {"error": "Invalid JSON"}, 400
    
    # Validate request
    req = AssistantChatRequest(**payload)
    
    # Get provider from config
    provider = req.provider or remediationProvider  # Use existing config
    
    # Call AI provider (reuse existing dispatching logic from #85)
    response_text = await dispatch_to_ai_provider(
        provider=provider,
        prompt=req.prompt,
        context=req.context.dict()
    )
    
    return {
        "response": response_text,
        "provider": provider,
        "context_used": req.context.dict(),
        "timestamp": datetime.now(UTC).isoformat()
    }
```

### Frontend Integration

The assistant sidebar (from #87) already has:
- Prompt input field
- Response display area
- Provider selector

Connect these to the endpoint:

```javascript
async function sendPromptToAssistant(prompt, context) {
    const response = await fetch('/api/assistant/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            prompt,
            context: {
                current_tab: tab,  // from App component state
                selected_run_id: selectedRun?.id,
                selected_items: Object.values(selected),
                // Include minimal relevant state
            },
            provider: remediationProvider
        })
    });
    
    const data = await response.json();
    setAssistantResponse(data.response);
}
```

---

## Security Considerations

### Input Validation
- Sanitize prompt input (no command injection)
- Validate context structure (no unexpected fields)
- Rate-limit prompts (e.g., max 10/minute per user)

### Context Privacy
- Don't include secrets in context (API tokens, passwords)
- Don't include large payloads (avoid serializing entire app state)
- Log prompts/responses carefully (may contain sensitive data)

### AI Provider Credentials
- Use existing provider auth from config (reuse remediationProvider setup)
- Never include credentials in context/prompt
- Validate provider URL is internal (localhost or Tailscale)

---

## Testing Plan

### Unit Tests
- Request/response model validation
- Invalid prompt handling (too long, empty, etc.)
- Provider selection (default vs override)

### Integration Tests
- Full request/response cycle with mock AI provider
- Context serialization correctness
- Error handling (provider unavailable, etc.)

### E2E Testing
- Frontend sends prompt with context
- Backend receives, processes, returns response
- Frontend displays response in sidebar

---

## Files to Modify

| File | Change | Size |
|------|--------|------|
| `backend/assistant_contract.py` | New: Request/response models | ~40 lines |
| `backend/server.py` | Add POST /api/assistant/chat endpoint | ~30 lines |
| `frontend/index.html` | Connect sidebar to endpoint | ~20 lines |
| `tests/api/test_assistant.py` | New: Unit + integration tests | ~80 lines |

---

## Existing Patterns to Reuse

1. **From `#85` (Remediation):** `dispatch_to_ai_provider()` function, provider config, error handling
2. **From `dispatch_contract.py`:** Request/response model pattern with Pydantic
3. **From `server.py`:** Error handling middleware, JSON response formatting

---

## Success Criteria

- [x] Endpoint accepts prompt + context, returns response
- [x] AI provider integration works (reuses existing config)
- [x] Frontend sidebar displays responses
- [x] Context is properly sanitized (no secrets leakage)
- [x] Rate limiting prevents abuse
- [x] Tests verify happy path + error cases

---

## Next Steps

1. **Get maintainer feedback** on Option B approach
2. **Design panel review** (if judgement:design feedback diverges)
3. **Implement** once consensus is reached
4. **Test** with Jules API and local Ollama
5. **Deploy** alongside assistant agent mode (#89)
