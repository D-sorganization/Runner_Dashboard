---
issue: 2603
summary: "Security scanner: panel q5 hardening. CI yield trusts only complete, fresh queue data; real scans run on the hub only; a scan heartbeat replaces the 14 h stale reclaim; leftover scan objects are swept; output is bounded; the build filter must be live (tmpfs marker plus a boot unit) and buildkitd DNS is pinned; the contract is revalidated at the scanned SHA and disabled is honoured; VOLUME images are refused; the digest is recorded; the protected-patch parser handles quoting and fails closed; advisory text is escaped; and the scheduler is window-first with failure backoff."
---
