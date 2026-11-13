---
issue: 3878
summary: "Runner-lease SQLite pilot (slice 2/2): read-only staging pilot (backend/runner_lease_pilot.py + scripts/runner_lease_pilot.py) runs dual-read, drain, cutover and rollback on a copy of a node's leases.yml against a scratch SQLite WAL store and prints a counts-only JSON evidence report; SqliteLeaseStore gains journal_mode(). No production wiring."
dl_state: "in_review"
next_step: "Run scripts/runner_lease_pilot.py on a staging spoke and post the JSON report on #3878; production cutover still needs recorded sign-off (#3874)."
branch: "claude/3878-lease-pilot"
---
