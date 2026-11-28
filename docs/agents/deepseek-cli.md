# DeepSeek CLI Connection & Provider Guide

This guide covers configuring the **DeepSeek API** route (`deepseek_api`, driven through Claude Code) as a headless staff CLI. DeepSeek is a paid route that is **explicitly opt-in per host and per role**, and currently runs draft-only qualification lanes first.

---

## 1. Overview & Architecture

DeepSeek has no standalone coding binary on fleet nodes. The provider runs the
**Claude Code CLI** against DeepSeek's Anthropic-compatible API endpoint. In the
Runner Dashboard architecture:

- **Provider Key:** `deepseek_api` (canonical `dashboard_id` in `backend/agent_remediation/provider_registry.py`; conductor ID `deepseek-api`).
- **Executable:** `claude` (Claude Code CLI), routed to DeepSeek via environment overrides.
- **Execution Mode:** Local subprocess execution (`local_exec`) on fleet nodes.
- **Dispatch Mode:** `future`.
- **Auth Mode:** `api_key`.
- **Unattended Execution:** `claude -p --model <model> --dangerously-skip-permissions <prompt>` with `ANTHROPIC_BASE_URL` pointed at DeepSeek's Anthropic-compatible endpoint.
- **Models:** `deepseek-flash` (low-cost worker), `deepseek-v4-pro` (primary).
- **Capabilities:** `code_edit`, `ci_fix`, `test_fix`.
- **Availability Probe:** the `claude` binary on `PATH` (`shutil.which("claude")`).
- **Required Env:** `DEEPSEEK_API_KEY`, gated by `STAFF_DEEPSEEK_API_ENABLED=true`.
- **Cloud Mutation:** disabled (`cloud_mutation_allowed=False`).
- **Opt-in:** per role via `providers:` in `staff/roles/*.yml`; not enabled fleet-wide by default. Draft-only qualification lanes are first.

### Route wiring

The staff module `backend/staff/deepseek_provider.py` builds the child-process
environment that repoints Claude Code at DeepSeek:

```python
{
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "<DEEPSEEK_API_KEY>",
    "ANTHROPIC_API_KEY": "",
}
```

`deepseek_environment()` raises `DeepSeekNotEnabled` unless **both**
`STAFF_DEEPSEEK_API_ENABLED=true` and a non-empty `DEEPSEEK_API_KEY` are present.
`deepseek_available()` additionally requires the `claude` binary. The paid route
never activates silently: the host operator must opt in, and the adapter keeps
the DeepSeek credential out of every other provider's environment.

---

## 2. Installation on Fleet Nodes

Install Claude Code globally and confirm the binary:

```bash
npm i -g @anthropic-ai/claude-code@latest
claude --version
```

Ensure the global npm `bin` directory is on `PATH` in the systemd service
drop-in for `runner-dashboard.service`. No DeepSeek binary is required — the
provider is reached entirely through Claude Code's Anthropic-compatible endpoint.

---

## 3. Authentication & Credentials

### Explicit enablement gate

DeepSeek is a **paid route** and is disabled unless the host operator opts in
explicitly. Both conditions must hold:

1. `STAFF_DEEPSEEK_API_ENABLED=true` — the enablement gate.
2. `DEEPSEEK_API_KEY=<key>` — the DeepSeek credential.

Create a root-only env file, for example `/etc/runner-dashboard/deepseek.env`:

```bash
STAFF_DEEPSEEK_API_ENABLED=true
DEEPSEEK_API_KEY=<deepseek-api-key>
```

Wire the file into the dashboard unit:

```ini
[Service]
EnvironmentFile=/etc/runner-dashboard/deepseek.env
```

The staff adapter maps the key into `ANTHROPIC_AUTH_TOKEN` (and blanks
`ANTHROPIC_API_KEY`) for the `claude` child process only, so other providers
never see it. No pay-per-use Anthropic or OpenAI key is involved; the explicit
gate preserves the fleet's subscription-only baseline.

### Models

- `deepseek-v4-pro` (primary)
- `deepseek-flash` (low-cost worker; used for delegated grunt work)

Only these two model IDs are accepted.

---

## 4. Verification

- Credentials probe: `GET /api/credentials` → `deepseek_api` probe (`ready` when the gate, key, and `claude` binary are all present).
- Provider registry: `GET /api/providers/registry` includes `deepseek_api` with `enabled: true` (opt-in per role).
- Headless smoke:

  ```bash
  STAFF_DEEPSEEK_API_ENABLED=true \
  DEEPSEEK_API_KEY=<deepseek-api-key> \
  ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic \
  ANTHROPIC_AUTH_TOKEN=<deepseek-api-key> \
  claude -p --model deepseek-flash "Say OK"
  ```

---

## 5. Related docs

- [Grok CLI guide](grok-cli.md) — standalone CLI provider pattern comparison.
- [Qwen Code CLI guide](qwen-cli.md) — another non-Anthropic provider routed through a CLI.
- [Durable provider credentials](../runbooks/durable-provider-credentials.md) — credential storage conventions.
