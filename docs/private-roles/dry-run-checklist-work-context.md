# Dry-run checklist: `work-context-pilot` private role

Part of epic #3093, slice 4 (issue #3097). Covers the **one** pilot private
persistent role for work context. This is a checklist a human (Dieter) runs
through **supervised**, against a real host-local overlay and context store.
It is not a substitute for the automated tests in
`tests/test_private_roles_work_context_pilot.py`, which exercise the same
paths with placeholder/fixture content only — no real work content lives in
this repository.

**No hub dispatch of this role until every item below passes.** The role
must not appear in any hub dispatch registry or routing table; this slice
does not add such wiring.

## Prerequisites

- [ ] Real overlay file (`RD_OVERLAY_PATH`) on the host defines exactly one
      `work-context-pilot`-equivalent `private_roles` entry, validated by
      `overlay_contract.load_overlay` (same schema as
      `tests/fixtures/overlay/work-context-pilot-overlay.json`, with real
      host/provider/template names Dieter chooses — never the fixture values).
- [ ] Real context store directory and host key exist only on the host, never
      committed to this repository.

## Checklist

1. **Degraded state when store absent.**
   - [ ] Point the overlay's `context_store_dir` at a path that does not yet
         exist (or temporarily rename the real store directory) and call
         `private_roles.read_private_context` for the role.
   - [ ] Confirm the call returns `PrivateContextUnavailable(reason="store-missing")`
         — not an exception, not any real content.
   - [ ] Restore the real store directory before continuing.

2. **Provider gate.**
   - [ ] Call `read_private_context` with a `provider_cli` that is *not* in
         the role's `providers` map for the host (or with the correct CLI but
         an unapproved host).
   - [ ] Confirm the call returns `PrivateContextUnavailable(reason="provider-not-approved")`.
   - [ ] Confirm the approved provider/host/CLI combination still succeeds.

3. **Redaction gate.**
   - [ ] Take real output derived from the role's context and pass it to a
         shared-surface writer (board thread, turnover, task inventory) only
         through an explicit classification span (`staff.private_surface_gate.tag`)
         at a non-`PUBLIC` level (the default — never tag real work content
         `PUBLIC` during this drill).
   - [ ] Confirm the content that lands on the shared surface is the fixed
         placeholder (`staff.private_surface_gate.PLACEHOLDER`), not the real
         content, by reading the surface back.
   - [ ] Confirm `staff.redaction.redact_sensitive_content` is the function
         actually invoked on that write path (it is already wired into every
         shared-surface writer per issue #3095; this step is a spot check,
         not new wiring).

4. **Audit entries.**
   - [ ] After each of the three drills above, inspect
         `private_context_audit.PrivateContextAuditStore.list_entries(role_id=...)`
         (or the equivalent `/api` surface, if one exists) and confirm one new
         metadata-only row was appended per call, with the matching outcome
         (`success`, `store-missing`, `provider-not-approved`), and that no row
         contains decrypted content.
   - [ ] Confirm a simulated audit-write failure (e.g. temporarily making the
         audit DB path unwritable) makes an otherwise-successful read fail
         closed with `reason="audit-write-failed"`, per issue #3096.

## Sign-off

- [ ] All four checklist sections above passed on the real host, supervised
      by Dieter.
- [ ] Dieter has explicitly confirmed this role may be wired into hub
      dispatch. Until that confirmation, the role stays dry-run-only and is
      never added to a dispatch registry or routing table.
