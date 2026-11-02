---
issue: 3591
summary: "Repair Codex worker and chat session contracts with native thread validation and structured read-only continuation"
dl_state: "in_review"
next_step: "Review the repair, qualify its release, then verify one bounded real Codex worker task."
branch: "codex/cli-worker-contracts"
---

The result nudge now verifies the observed Codex thread against the requested
original session. Mismatch diagnostics retain both thread identifiers; output
and usage remain available even when the result is rejected.

Validation: new fake-stream and execution-path regressions failed before the
repair; focused continuity, initial-session and extraction tests pass afterward.
Live worker acceptance requires a qualified deployment and remains pending.

Also fixes #3592, the required Windows push-hook portability prerequisite:
unavailable Unix sockets raise OSError at the existing fail-closed WhoIs
boundary; no alternate transport or trust policy is introduced.

Also fixes #3595: Codex-backed chat requests JSON events, uses the supported
resume subcommand with explicit read-only sandboxing, and retains the native
thread ID over legacy Session text in assistant prose. Fallback text and JSON
prose preserve line breaks and summary capsule handling.

Startup, continuation and output regressions failed first; installed Codex
0.164.0 accepted all four generated Codex/Ollama commands in help-only parser
checks. No live role, credential, permission policy or deployment was changed.

Worker resume also retains the initial explicit provider, model, sandbox,
network and git-directory options instead of rebuilding a reduced command.
Four failing Codex/Ollama regressions cover default and explicit models;
installed 0.164.0 help-only probes accept the preserved parent options before
the resume subcommand. This retains the initial run's permissions and routing.

Native-thread validation covers Ollama's Codex-backed nudge too. Three cases
failed before applying the same existing rejection to missing/mismatched thread
IDs; matching-thread results, usage and transcript evidence remain intact.
