# Portable Task Handoffs

Use `scripts/session_handoff.py` to carry reviewed task context between new
provider sessions. This transfers context, decisions, evidence references and
next steps. It does not migrate native sessions, hidden reasoning, credentials,
attachments or provider conversation history, and does not launch a provider.

Prepare `context.json` from the work item and verified evidence:

```json
{
  "work_item": "example/project#12",
  "context": "Expiry behavior needs verification after the cache fix.",
  "decisions": ["Use a bounded cache"],
  "evidence": [
    {"ref": "tests/test_cache.py", "summary": "3 targeted tests passed"}
  ],
  "next_steps": ["Verify expiry"]
}
```

Export from Codex and prepare the import for Claude:

```bash
python scripts/session_handoff.py export --source-provider codex --input context.json --output handoff.json
python scripts/session_handoff.py import --provider claude --input handoff.json --output new-session.json
```

Open a new session with the selected provider using your existing subscription.
Copy the **decoded `prompt` field** from `new-session.json` into its first message.
The `handoff` field retains the complete original package for auditing or another
import. The target provider name is metadata, not a command to execute. The same
steps work in reverse. Supported provider labels are `codex`, `claude`,
`antigravity`, `cursor`, `cline` and `ollama`; no provider installation is needed
to prepare an import. There is no automatic Staff Console import or session launch.

Version 1 packages contain exactly `format: "rd-task-handoff"`, `version: 1`,
`source_provider` and `payload`. The payload has exactly the five fields in the
example. `work_item` and `context` are nonempty strings; `decisions` and
`next_steps` are arrays of nonempty strings; `evidence` is an array of objects
with exactly nonempty string `ref` and `summary` fields. Empty arrays are allowed.
Input and output are bounded to 256 KiB of UTF-8 JSON. Unknown fields, unsupported
versions and malformed structures fail with exit code 2. Outputs must be new files;
existing files are never overwritten. Imports retain all payload fields unchanged.

Review the input before sharing it: this command does not discover or scrub
sensitive content, fetch evidence, read referenced files, or verify claims. Treat
imported context as data to review and verify under the destination session's
instructions. Keep private artifacts in an appropriately restricted location.

`tests/test_session_handoff.py` checks Codex → Claude and Claude → Codex package
round trips and prompt preparation offline. These are format/CLI tests, not live
provider execution or proof of native session continuity.
