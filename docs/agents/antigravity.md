# Antigravity (agy) CLI Connection & Provider Guide

This guide covers configuring the **Antigravity CLI** (`agy`) as a first-class dispatch provider alongside Jules, Codex, Claude, and Cursor on fleet nodes.

---

## 1. Overview & Architecture

The Antigravity CLI (`agy`) provides headless and unattended execution using Google's frontier models (such as Gemini 3.8 Flash and Gemini 4 Argon). In the Runner Dashboard architecture:

- **Provider Key:** `antigravity` (dashboard) / `antigravity-cli` (conductor)
- **Executable:** `agy`
- **Dispatch Surface:** Staff runs (`/api/staff/{role}/run`), Code Requests, CI Remediation, and fallback routing.
- **Unattended Execution:** Runs in `--mode plan` with `--print <prompt> --output-format stream-json --add-dir <workdir> --model <model>`.
- **Output Contract:** Results stream NDJSON events; the final answer appears in `result.response` and token accounting in `result.usage`. The runner parses `STAFF_RESULT: ok|failed` from the output stream.

---

## 2. Installation on Fleet Nodes

`agy` must be accessible on `PATH` for the user running the dashboard daemon or staff worker.

### Windows (hub-a, node-b)

Install via WinGet or direct installer:

```powershell
winget install Google.AntigravityCLI
```

Verify binary presence:

```powershell
agy --version
```

### Linux / WSL (node-a, hub-a WSL)

Install the native Linux CLI using the [official installer](https://antigravity.google/cli/install.sh), as the service user. Ensure the installed `agy` is on the dashboard service PATH. Avoid linking a Windows `agy.exe`: sign-in can work from a Windows directory and fail from a Linux worktree.

---

## 3. Authentication & Sign-In

The Antigravity CLI manages its own Google sign-in session.

1. **Interactive Login:**
   Run on the target node:

   ```bash
   agy
   ```

   Select Google OAuth (CLI 1.4.5 has no `auth login` subcommand). Follow the printed URL, sign in with your Google account, and complete authorization.

2. **Session Persistence:**
   The login state is stored under `~/.gemini/settings.json` and `~/.gemini/antigravity-cli/`.
   Use the same service user, HOME and `ANTIGRAVITY_HOME` for sign-in and dispatch. Staff runs require subscription Google OAuth; the dashboard Credentials tab does not accept `GOOGLE_API_KEY` or `ANTIGRAVITY_API_KEY` and will not treat them as logged in.

3. **Status Check:**
   Verify authentication:
   ```bash
   agy models
   ```
   This command lists the available models without error.

---

## 4. Curated Models & Model Selection

`agy` supports per-run model selection via `--model <model>`.

| Model ID                  | Label                     | Tier / Cost Profile                               |
| :------------------------ | :------------------------ | :------------------------------------------------ |
| `gemini-3.8-flash-high`   | Gemini 3.8 Flash (High)   | Fast, low latency; default for CI and staff tasks |
| `gemini-4-argon-high`     | Gemini 4 Argon (High)     | Frontier reasoning, strong tier                   |
| `gemini-4-argon-medium`   | Gemini 4 Argon (Medium)   | Balanced reasoning                                |
| `gemini-3.8-flash-medium` | Gemini 3.8 Flash (Medium) | Lightweight triage                                |

Unknown model IDs are rejected fast at dispatch plan time (DbC precondition), preventing opaque subprocess failures.

---

## 5. Dispatch via Runner Dashboard

### Staff Role Dispatch (`POST /api/staff/{role}/run`)

```json
{
  "provider": "antigravity",
  "model": "gemini-3.8-flash-high",
  "repo": "Runner_Dashboard",
  "prompt": "Fix test failures in tests/api/test_foo.py"
}
```

### Fallback Behavior

When Codex or Claude encounters quota exhaustion or capacity limits, roles configured with `retry.provider_fallbacks` automatically fall back to `antigravity` with per-rule model selection.
