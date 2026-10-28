# Current handoff — Provider CLI test isolation (#2239)

- **Repository / worktree:** Runner_Dashboard, assigned isolated worktree; branch `staff/issue-remediator-2239-819de2`; commit pending; PR pending.
- **Governing issue:** #2239 — tests must use fake provider executables and must never run host Codex, Claude or agy CLIs.
- **Completed:** Added an autouse `subprocess.Popen` guard for provider executable names, a sentinel regression test, and made the approved local dispatch test explicitly dispatch through the temporary fake CLI from the shared staff fixture.
- **Validation:** Targeted pytest could not start: default pytest uses Python 3.10, which lacks `datetime.UTC`; Python 3.11 has no installed pytest module. The default pytest path also cannot create its conftest temp directory under `/home/fleet-operator/.config` in this sandbox. No provider CLI was run.
- **Next step:** Run the targeted dispatch and sentinel tests in the configured Python 3.11 test environment, then complete pre-PR checks and open a ready PR.

## Barb Continuity Follow-Up (#2199, PR #2246)

- The summary parser now keeps recognized malformed, incomplete or ambiguous metadata out of persisted replies, matching the streaming filter. Quoted and nested examples remain ordinary reply text.
- Combined context/summary credentials require an explicit operator opt-in and separate provisioning of exactly `memory.read` plus `memory.shared.summarize`; do not grant generic `memory.write`. Each original caller also needs summary permission.
- Validation: three regression cases failed before the parser fix; all 38 summary/streaming/result tests pass afterward. Complete canonical and ordinary commit/pre-push checks before publication.
- Runtime acceptance remains pending the qualified canonical hub-a API deployment and actual provider save acknowledgement. Preserve its existing memory and conversations; no node-a-local fallback.

## Staff run env + scheduler cursor (#2485)

- Staff run env now sets `PYTHONPATH` to the dashboard backend dir (gh wrapper and credential helper import `staff.*` from any repo cwd); the pre-push hook points at the dashboard's own `git_hooks.py` using the running interpreter.
- Scheduler reseeds a cursor stale by two or more slots to now; one missed slot still fires once. Re-enabling a long-disabled node no longer replays old slots.
- Next step: after merge and deploy, re-enable the staff scheduler on the node and confirm `/api/staff/schedule` shows a future `next_fire`.
