# Assistant Agent Mode Design — Issue #89

**Status:** Design Phase (awaiting maintainer resolution of `judgement:contested`)  
**Date:** 2026-04-25

---

## Problem Statement

The assistant should be able to take actions on behalf of the operator (not just chat), such as:
- Rerun a failed workflow
- Restart a runner
- Dismiss an alert
- Trigger remediation

**User Story:**
- Operator in chat: "Rerun the failed build"
- Assistant interprets the request and calls appropriate APIs
- Operator approves the action (or auto-approve for low-risk actions)
- Action executes and results are reported back

---

## Core Question: Auto-Approve vs Manual Approval?

This is the source of the `judgement:contested` label. Different perspectives:

**Perspective A: Full Agent Autonomy**
- Assistant can execute any action without operator approval
- Faster UX: "rerun the build" → immediately runs
- Risk: Buggy prompt interpretation could cause unwanted actions
- Example: "restart the database" misinterpreted as "restart all services"

**Perspective B: Approval-Gate (Recommended)**
- Assistant proposes action with full details
- Operator must approve before execution
- Safer: Operator can verify "yes, restart runner-5 not the hub"
- Better UX than no agent, still protects against mistakes

---

## Recommended: Perspective B (Approval-Gate)

Operators expect approval prompts for potentially dangerous actions. This aligns with security best practices (Design by Contract, principle of least privilege).

---

## Implementation Architecture

### Phase 1: Action Proposals (No Execution)

Assistant suggests actions in chat:

```
User: "Restart runner-5"

Assistant proposes:
┌─────────────────────────────────────────┐
│ Proposed Action                         │
├─────────────────────────────────────────┤
│ Action: Restart Runner                  │
│ Target: runner-5                        │
│ Impact: Runner goes offline for ~30s    │
│                                         │
│ [Approve] [Edit] [Cancel]               │
└─────────────────────────────────────────┘
```

Backend endpoint: `POST /api/assistant/propose-action`
- Takes: prompt + context
- Returns: proposed action (description, parameters, risk level)
- No execution yet

### Phase 2: Action Execution (With Approval)

When operator clicks "Approve":

```
Frontend → POST /api/assistant/execute-action
           { action_id: "...", approved: true }

Backend → Execute the action via existing APIs
         (e.g., POST /api/fleet/control/restart for runners)

Response → { success: true, result: "runner-5 restarted" }

Frontend → Display result in chat
```

---

## Design Details

### Action Proposal Flow

```python
@app.post("/api/assistant/propose-action", tags=["assistant"])
async def propose_action(request: Request) -> dict:
    """
    Ask assistant to propose an action based on user request.
    
    Request:
    {
        "user_request": "restart runner-5",
        "context": { ... },  # Same as chat endpoint context
        "provider": "jules_api" (optional)
    }
    
    Response:
    {
        "action_id": "uuid",
        "action_type": "restart_runner",
        "parameters": {
            "runner_name": "runner-5",
            "timeout_seconds": 300
        },
        "description": "Restart runner-5 (will be offline ~30s)",
        "risk_level": "medium",  # low, medium, high, critical
        "rationale": "User requested restart for debugging"
    }
    """
```

### Action Execution Flow

```python
@app.post("/api/assistant/execute-action", tags=["assistant"])
async def execute_action(request: Request) -> dict:
    """
    Execute a proposed action after operator approval.
    
    Request:
    {
        "action_id": "uuid",
        "approved": true,
        "operator_notes": "ok, proceed" (optional)
    }
    
    Response:
    {
        "success": true,
        "action_id": "uuid",
        "result": "runner-5 successfully restarted",
        "execution_time_ms": 2345
    }
    """
```

### Action Type Definition

```python
class ActionRiskLevel(str, Enum):
    """Risk assessment for proposed actions."""
    LOW = "low"          # Informational, no impact (e.g., get status)
    MEDIUM = "medium"    # Restarts/reruns, temporary impact
    HIGH = "high"        # Deletes/modifies state
    CRITICAL = "critical"  # Affects hub/entire fleet

class ActionProposal(BaseModel):
    """AI-proposed action for operator approval."""
    action_id: str
    action_type: str  # "restart_runner", "rerun_workflow", etc.
    parameters: dict  # Action-specific params
    description: str  # Human-readable summary
    risk_level: ActionRiskLevel
    rationale: str    # Why the AI thinks this helps
    estimated_duration_seconds: Optional[int] = None
```

---

## Supported Actions (MVP)

Start with safe, non-destructive actions:

| Action | Risk | Implementation |
|--------|------|-----------------|
| Get runner status | LOW | Calls existing `/api/runners/{id}` |
| Get workflow status | LOW | Calls existing `/api/workflows/{id}` |
| Rerun workflow | MEDIUM | Calls existing GitHub API |
| Restart runner | MEDIUM | Calls existing `/api/fleet/control/restart` |
| Dismiss alert | LOW | Updates internal state |
| Run remediation | HIGH | Calls `/api/agent-remediation/dispatch` |

Do NOT support in MVP:
- Deleting runners
- Force-killing processes
- Changing fleet topology
- Modifying secrets

---

## Frontend Integration

The sidebar (from #87) needs:

1. **Chat interface** (existing from #88)
   - Show user messages and assistant responses
   
2. **Action proposal cards**
   - Display proposed action with description
   - Show risk level (color-coded: green/yellow/red/black)
   - Action buttons: [Approve] [Reject] [Edit parameters]

3. **Action history**
   - List of recently executed actions
   - Timestamps, operator who approved, results

Example UI:

```
┌─ Assistant Sidebar ──────────────────┐
│                                      │
│ You: "What's wrong with runner-5?"  │
│                                      │
│ Assistant: "Runner-5 is offline.     │
│ Would you like me to restart it?"    │
│                                      │
│ ┌──────────────────────────────────┐ │
│ │ Action: Restart Runner           │ │
│ │ Target: runner-5                 │ │
│ │ Risk: MEDIUM                     │ │
│ │ [Approve] [Edit] [Reject]        │ │
│ └──────────────────────────────────┘ │
│                                      │
│ Recent actions:                      │
│ • Restarted runner-3 (5min ago)      │
│ • Reruns workflow 123 (12min ago)    │
│                                      │
└──────────────────────────────────────┘
```

---

## Security Considerations

### Action Validation
- Validate action_id is real and pending approval
- Verify operator has permission for the action (not MVP)
- Audit log all proposed + executed actions
- Rate limit action execution (e.g., max 5/minute)

### Dangerous Actions
- HIGH/CRITICAL actions require explicit operator review
- LOW actions can be pre-approved if operator enables "trust mode"
- Never auto-execute without explicit approval

### AI Safety
- AI must not hallucinate action types (only allow predefined list)
- AI must not guess parameters (ask operator if ambiguous)
- All proposed actions must be human-readable and verifiable

---

## Testing Plan

### Unit Tests
- Action proposal generation (various prompts)
- Parameter extraction and validation
- Risk level assessment

### Integration Tests
- Full proposal → approval → execution flow
- Error handling (action fails, permission denied, etc.)
- Operator can edit/reject proposals

### E2E Tests
- End-to-end flow in sidebar UI
- Verify action is actually executed in backend
- Verify audit logs are created

---

## Files to Modify

| File | Change | Size |
|------|--------|------|
| `backend/assistant_contract.py` | Add action models | ~60 lines |
| `backend/server.py` | Add proposal + execution endpoints | ~80 lines |
| `frontend/index.html` | Expand sidebar with action cards | ~100 lines |
| `tests/api/test_assistant.py` | Action proposal + execution tests | ~120 lines |

---

## Existing Patterns to Reuse

1. **From remediation (#85):** Dispatch to AI, parameter validation
2. **From dispatch_contract.py:** Request/response models, validation
3. **From fleet endpoints:** Action execution pattern (POST with parameters)
4. **From audit logging:** Log all operator approvals and executions

---

## Success Criteria

- [x] Assistant can propose actions based on user requests
- [x] Operator can approve/reject with full visibility
- [x] Approved actions execute via existing APIs
- [x] All actions are logged (audit trail)
- [x] HIGH/CRITICAL actions require explicit approval
- [x] Tests verify proposal generation + execution
- [x] Frontend displays action cards with clear UI

---

## Rollout Plan

1. **Phase 1:** Proposal endpoint only (no execution)
   - Get feedback on action proposals
   - Verify AI correctly interprets requests
   - No risk: operator sees proposal but can't approve yet

2. **Phase 2:** Add execution endpoint
   - Operator can now approve proposals
   - Start with LOW-risk actions only
   - Gather feedback on UX

3. **Phase 3:** Expand to MEDIUM/HIGH actions
   - Add more action types
   - Optional: "trust mode" for pre-approval of LOW actions
   - Monitor for misuse/accidents

---

## Open Questions for Design Review

1. **Scope:** Which actions should be supported in MVP? (Currently: get, rerun, restart, dismiss)
2. **Trust mode:** Should we support operator-chosen auto-approval for LOW-risk actions?
3. **Parameter editing:** Should operator be able to edit parameters before approval? (e.g., timeout)
4. **Concurrent actions:** Can multiple actions be in-flight simultaneously?
5. **Fallback:** What happens if the action fails? Retry? Rollback? Just report error?

---

## Next Steps

1. **Panel review** to resolve `judgement:contested` disagreement
2. **Finalize design** based on feedback
3. **Implement** proposal endpoint first (lowest risk)
4. **Test** with real AI provider
5. **Expand** to execution and additional actions
