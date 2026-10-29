# Obsidian RO-1: authority/provenance contract (#3506)

Parent epic: #3505 (conditional read-only Obsidian projection). This is slice 1
of 3: a characterization of **existing** authority/provenance/concurrency
behavior, with tests pinning each row (`tests/unit/test_memory_authority_provenance_contract.py`
plus the existing module tests it cross-references). No synchronization,
export, or production-state change happens in this slice — see
[fleet-deferred-validation.md](https://github.com/D-sorganization/Repository_Management/blob/main/docs/fleet-deferred-validation.md)
for the standard this follows on anything that would need a later, separately
authorized observation path (live deployed-topology/runtime evidence is
explicitly **unverified** by this document; it characterizes source code only).

## Matrix: canonical source, owner, scope, provenance

| Surface | Canonical source | Owner module | Auth/scope | Version/provenance | CAS / tombstone? |
| --- | --- | --- | --- | --- | --- |
| Manual + Playbooks (#3486-#3490) | Directory of YAML files (`STAFF_PLAYBOOKS_DIR` override, else a sibling `Repository_Management/staff/playbooks` checkout) | `backend/staff/playbooks/store.py`, `assignments.py` | `require_fleet_peer` (read), role-eligibility check against `role_refs` (assign) | `schema_version`/`version` fields in YAML; render ETag is a content hash of the resolved playbook, not of the directory | No CAS, no DB at all — see [below](#correction-manual-playbooks-cache-has-no-database). Assignments are in-memory (`assignments.py`), not persisted. |
| Role permissions | Directory of YAML files (mirrors playbooks resolution) | `backend/staff/roles.py` (`load_roles()`) | N/A at load time; consumers call `identity.require_scope()`/`principal_has_scope()` with domain-specific scope strings (e.g. `memory.write`, `memory.review`, `fleet.read`/`maintain`/`control`) — **no generic `staff.read`/`staff.write` pair exists; do not assume one** | Implicit in file content; no explicit version field inventoried here | No CAS; reload is cache-invalidated by `clear_roles_cache()`, same shape as playbooks |
| Memory entries: session_digest / decision / task / transcript (generic) | SQLite `memory_entries` table | `backend/memory/store.py` (`AssistantMemoryStore.append`) | `memory.write` scope via `backend/identity.py` | `created_at`, `supersedes_id`/`superseded_by` chain | **No real CAS on supersession** — `append()`'s link-update is a best-effort `UPDATE ... WHERE superseded_by IS NULL`, not atomic with the insert. See `test_append_has_no_cas_on_concurrent_supersession`. |
| Memory entries: session_digest idempotent append | SQLite `memory_entries` + `memory_idempotency` | `AssistantMemoryStore.append_idempotent` | `memory.write` | `idempotency_key` + `fingerprint` digest | Real CAS: `BEGIN IMMEDIATE` transaction, same-key-same-fingerprint replays, same-key-different-fingerprint raises `idempotency_key_reused`. See `test_idempotent_append_is_a_real_digest_cas`. |
| Memory entries: lesson review lifecycle | SQLite `memory_entries` + `run_stores` run-binding | `backend/staff/lesson_provenance.py`, `backend/memory/service.py` | `memory.write` to propose, `memory.review` (librarian) to accept/quarantine; `authenticated_lesson_run()` binds acceptance to the exact active run | `review_status` (`proposed`→`accepted`/`quarantined`/`retired`/`promoted`), `reviewed_by`, `accepted_at` | Real CAS via `update_review()`'s `expected_review_status` match — already characterized by `tests/unit/test_memory_lessons.py::test_review_rejects_a_stale_racing_status_transition`; **not re-tested here** (see `test_update_review_is_a_real_cas_unlike_append` for the generic-entry version of the same contract, used as a contrast case against plain `append()`). Lessons cannot be superseded through the generic `supersedes_id` path at all — already characterized by `tests/unit/test_memory_lessons.py` (the `"lessons cannot be superseded through generic append"` assertion); not duplicated here. |
| Holds / directives (#2657, #2658, #2880) | Out of scope for this slice — not characterized here. Preserved unchanged per standing fleet rule; any future slice touching them needs an explicit owner decision, not an inference from this matrix. | n/a | n/a | n/a | n/a |
| Turnovers / shift logs | Out of scope for this slice — no single canonical module was characterized with contract tests here; flagged as an open gap for slice 2/3 scoping, not assumed absent. | n/a | n/a | n/a | n/a |

### Correction: manual/playbooks cache has no database

#3505's tracking language calls the playbooks/manual store a "DB-cache."
Reading `backend/staff/playbooks/store.py` and confirming with
`test_playbooks_store_has_no_database_only_an_mtime_cache`: there is **no
database** — it is a pure in-process cache keyed by `(directory, per-file
mtime)`, rebuilt by re-reading YAML straight off disk whenever any watched
file's mtime changes (even with unchanged content). Any later slice that
assumes a persisted/rebuildable DB table for playbooks should re-verify that
assumption rather than relying on #3505's wording.

### Inventory: export/redaction/audit helpers (scoped down)

Per this slice's own dependency note ("exit... slice 2 depends on this
issue's accepted schema/evidence gate"), a full inventory of every
export/redaction/audit helper with per-helper test-coverage mapping is
deferred to slice 2 (#3507), where it is actually needed to design the bounded
exporter. What is confirmed to exist and should be reused rather than
duplicated: `backend/memory/redact.py` (`contains_secret`, `redact_memory_text`,
applied inline at append time in `memory/service.py`), `backend/staff/redaction.py`
(`redact_sensitive_content`, `redact_value`), and `backend/staff/audit.py`
(`export_audit_csv`/`export_audit_ndjson`). **Gap, stated explicitly per the
issue's "identify unknowns" requirement:** no single allowlisted export
schema module exists yet across these — slice 2 is where that schema gets
designed, not retrofitted here.

## Go/no-go checklist for slice 2 (#3507)

- [x] Generic append/supersedes_id semantics characterized, including the
  concurrent-supersession gap (no CAS, no tombstone).
- [x] Digest-idempotency CAS (`append_idempotent`) characterized and
  contrasted with the above.
- [x] Lesson review CAS and the lesson-supersession boundary confirmed
  already covered by existing tests (cross-referenced, not duplicated).
- [x] Manual/playbooks store confirmed directory/YAML-sourced with an
  in-memory mtime cache — the "DB-cache" assumption in #3505 is corrected.
- [x] Role/permission scope-string taxonomy confirmed domain-specific
  (`memory.*`, `fleet.*`); no generic `staff.read`/`staff.write` pair exists.
- [ ] **No-go as written for holds/directives and turnovers**: out of scope
  here, not characterized — slice 2 must not assume contract coverage for
  these surfaces from this document.
- [ ] **No-go on a finished export schema**: helpers to reuse are identified,
  but the "minimal allowlisted export schema" itself is explicitly deferred
  to slice 2's own design work, not produced here.
- All evidence above is source-code-level (unit tests against synthetic
  fixtures); no live/deployed-topology runtime evidence was collected or is
  claimed by this document.
