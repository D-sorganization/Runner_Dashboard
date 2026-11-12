# Qwen Code CLI Connection & Provider Guide

This guide covers configuring **Qwen Code** (`qwen`, provider ID `qwen_cli`) as a headless staff CLI under a QwenCloud **Token Plan** subscription.

---

## 1. Overview & Architecture

Qwen Code is the terminal coding agent from Alibaba/Qwen (`@qwen-code/qwen-code`). In the Runner Dashboard architecture:

- **Provider Key:** `qwen_cli` (canonical ID in `backend/agent_remediation/provider_registry.py`; aliases `qwen`, `qwen-code`)
- **Executable:** `qwen`
- **Execution Mode:** Local subprocess execution (`local_exec`) on fleet nodes.
- **Unattended Execution:** `qwen --auth-type openai --output-format stream-json --approval-mode auto-edit --channel CI --model <model> <prompt>` (positional prompt; `-p` is deprecated).
- **Safety & Permissions (#1586):** Staff adapters use `auto-edit` for unattended runs and `plan` for read-only chat. Fleet adapters **never** pass `--yolo` or `-y`.
- **Auth:** QwenCloud Token Plan subscription key (`sk-sp-…`), not pay-as-you-go OpenAI API billing.
- **Failover:** Opt-in per role via `providers:` in `staff/roles/*.yml`. Not in `config/provider_failover.json` by default.

---

## 2. Installation on Fleet Nodes

```bash
npm i -g @qwen-code/qwen-code@latest
qwen --version
```

Ensure the global npm `bin` directory is on `PATH` in the systemd service drop-in for `runner-dashboard.service`.

---

## 3. Authentication & Credentials

### Token Plan subscription

1. Obtain a **Token Plan** key from QwenCloud (prefix `sk-sp-`).
2. Create a root-only env file, for example `/etc/runner-dashboard/qwen.env`:

   ```bash
   QWENCLOUD_TOKEN_PLAN_API_KEY=<sk-sp-token-plan-key>
   OPENAI_BASE_URL=https://token-plan.maas.qwencloudapi.com/compatible-mode/v1
   OPENAI_MODEL=qwen3.8-flash
   ```

   `BAILIAN_TOKEN_PLAN_API_KEY` is accepted as an alias for the same key.

3. Wire the file into the dashboard unit:

   ```ini
   [Service]
   EnvironmentFile=/etc/runner-dashboard/qwen.env
   ReadWritePaths=%h/.qwen
   ```

The staff adapter copies the plan key into `OPENAI_API_KEY` only for the `qwen` child process so other providers never see it.

### Models on the Token Plan

- `qwen3.8-flash` (default)
- `qwen3.8-max`
- `qwen3.7-plus`
- `qwen3.7-max`
- `qwen3.6-flash`
- `qwen4-max-preview`

---

## 4. Verification

- Credentials probe: `GET /api/credentials` → `qwen_cli` probe (`ready` when binary and key are present).
- Provider registry: `GET /api/providers/registry` includes `qwen_cli` with `enabled: true` (opt-in per role).
- Headless smoke (service user):

  ```bash
  qwen --auth-type openai --approval-mode auto-edit --channel CI -m qwen3.8-flash -o json "Say OK"
  ```

Use `--output-format stream-json` when parsing line-oriented staff output.

---

## 5. Related docs

- [Durable provider credentials](../runbooks/durable-provider-credentials.md) — Token Plan exception for `qwen`
- [Grok CLI guide](grok-cli.md) — standalone CLI pattern comparison
