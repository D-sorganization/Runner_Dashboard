# Current handoff — Provider CLI test isolation (#2239)

- **Repository / worktree:** Runner_Dashboard, assigned isolated worktree; branch `staff/issue-remediator-2239-819de2`; commit pending; PR pending.
- **Governing issue:** #2239 — tests must use fake provider executables and must never run host Codex, Claude or agy CLIs.
- **Completed:** Added an autouse `subprocess.Popen` guard for provider executable names, a sentinel regression test, and made the approved local dispatch test explicitly dispatch through the temporary fake CLI from the shared staff fixture.
- **Validation:** Targeted pytest could not start: default pytest uses Python 3.10, which lacks `datetime.UTC`; Python 3.11 has no installed pytest module. The default pytest path also cannot create its conftest temp directory under `/home/dieterolson/.config` in this sandbox. No provider CLI was run.
- **Next step:** Run the targeted dispatch and sentinel tests in the configured Python 3.11 test environment, then complete pre-PR checks and open a ready PR.
