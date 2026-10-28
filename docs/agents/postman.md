# Postman relay architecture (#3226)

Panel synthesis `th_e54ff7081c60` (2026-10-27): build **one model-free postman relay** with a **per-provider delivery adapter**, not separate persistent LLM agents per provider. Routing is deterministic and costs **zero model tokens while idle**.

## Decision summary

| Topic | Choice |
| ----- | ------ |
| Core | Single relay leader + delivery adapters (Claude push, Codex/agy hook or poll) |
| Canonical transport | Hub threads `GET/POST /api/v1/staff/threads` (ADR 0007, `scripts/rd_threads_envelope.py`) |
| Cloud Duet bridge | Barbara `barb-inbox` store-and-forward (#3217); same rd-threads envelope (#3072) |
| Topology | rd-barb #3117: leader on Desk, warm standby on ControlTower; shares #3073 leader lease when bound (#3237) |
| Safety | Refuse secrets (never redact), recipient allowlist, no dispatch/staff-run authority, Dieter only via rd-barb webhook (#3233) |
| Role file | RM#2461 stays decoupled |

Related issues: [#3226](https://github.com/D-sorganization/Runner_Dashboard/issues/3226), [#3217](https://github.com/D-sorganization/Runner_Dashboard/issues/3217), [#3073](https://github.com/D-sorganization/Runner_Dashboard/issues/3073), [#3117](https://github.com/D-sorganization/Runner_Dashboard/issues/3117), [RM#2461](https://github.com/D-sorganization/Repository_Management/issues/2461).

## Provider messaging capability matrix

As of the probe run date (`scripts/postman_provider_caps.py` → `as_of`), **Codex CLI** and **agy (Antigravity / Gemini CLI)** have **no native session-to-session messaging**. They integrate through **hooks** (`UserPromptSubmit` / `Stop` for Codex; `AfterAgent` / `AfterTool` / `Stop` or fleet hook group for agy) or fall back to **hub poll**.

| Provider | Native mesh | Relay delivery | Detection (read-only) |
| -------- | ----------- | -------------- | --------------------- |
| Claude Code | Cross-session push (local socket / Channels) | `push` when `CLAUDE_CODE_SSE_PORT` is set or `settings.json` has `channels.enabled` | Env var **names** + config flags only; token values never read |
| Codex CLI | None | `hook` when `~/.codex/hooks.json` lists `UserPromptSubmit` or `Stop` commands; else `poll` | Parse hooks JSON locally |
| agy | None | `hook` when Antigravity/Gemini hook files list events; else `poll` | `ANTIGRAVITY_HOME` / `~/.gemini` settings and `.agents/hooks.json` |

Run on each fleet node (no network, no model spend):

```bash
python3 scripts/postman_provider_caps.py
```

Example shape:

```json
{
  "as_of": "2026-10-27",
  "providers": {
    "claude_code": {"delivery": "push", "evidence": ["env:CLAUDE_CODE_SSE_PORT is set"]},
    "codex_cli": {"delivery": "hook", "evidence": ["hooks.json:Stop configured"]},
    "agy": {"delivery": "poll", "evidence": ["no hook events configured; agy has no native session-to-session messaging"]}
  }
}
```

## Implementation slices (#3226)

1. **#3233** — relay guard (secret refusal, allowlist, hop ≤3, dedup, dead-letter).
2. **#3234** — this document + provider capability probe (you are here).
3. **#3235** — hub-thread delivery port (after #3233).
4. **#3236** — Barbara `barb-inbox` bridge (gated on #3217, #3235).
5. **#3237** — bind relay to #3073 lease in #3117 topology.

Existing coordination postman (`backend/coordination/postman.py`, board/bus adapters #3151) delivers board traffic into the agent bus; the relay slices above add guarded, provider-aware session delivery on top of that foundation.
