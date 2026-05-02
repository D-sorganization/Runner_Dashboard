# Dispatch Schema Migrations

## v1 (current) — `dispatch-envelope.v1`

Initial production schema. All fields documented in `dispatch/envelope.py`.

### v1 → v2 transition rules (not yet shipped)

Reversible schema changes follow a two-step process per CLAUDE.md:

1. **Step 1 (additive):** Ship new fields to the dashboard with defaults; old
   clients continue to work.
2. **Step 2 (removal):** Once all clients emit the new shape, remove the old
   fields in a subsequent release.

The `migrate_envelope_v1_to_v2` shim in `dispatch_contract.py` is the hook
point for this migration when v2 is designed.

### Backward compatibility guarantee

`backend/dispatch_contract.py` is a thin re-export shim. All existing imports
(`from dispatch_contract import X`, `import dispatch_contract`) continue to
resolve identically. No callers need updating for the v1 refactor.
