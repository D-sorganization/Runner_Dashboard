---
issue: 2411
summary: "Preserve codebase retrieval provenance and support run-bound CLI searches with bounded UTF-8 snippets."
---

## Changed

- Codebase client searches preserve citation anchors, authority and scoring metadata, use citation identities, and cap snippets at 400 UTF-8 bytes.
- Added `fleetctl query-codebase` with explicit or `STAFF_RUN_ID` retrieval recording through the owner-authorized API query parameter. Authorization errors propagate without an unrecorded fallback.

## Validation

- Regression tests cover multilingual snippets, exact provenance, run identity precedence, invalid identities, authorization failures and the shared CLI command.
